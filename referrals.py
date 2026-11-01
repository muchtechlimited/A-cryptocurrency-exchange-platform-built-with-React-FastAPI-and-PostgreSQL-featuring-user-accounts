"""
Referral routes.

  GET  /referrals/me                    - my code, totals, and referral list (also settles my due rewards)
  GET  /referrals/admin                 - all referrals, optional ?status=
  POST /referrals/admin/{id}/review     - approve a flagged referral / cancel any unpaid one
  POST /referrals/admin/settle          - pay every due reward now (cron-friendly)

Rules live in app/utils/referral_service.py.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.models.referral import Referral, ReferralStatus
from app.models.user import User
from app.schemas.referral import (
    AdminReferralOut,
    AdminReferralReview,
    ReferralItemOut,
    ReferralSummaryOut,
    SettleResult,
)
from app.utils.audit import log_admin_action
from app.utils.referral_service import HOLD_DAYS, REFERRAL_REWARD_NGN, ensure_referral_code, settle_due
from datetime import timedelta

router = APIRouter()

_CUSTOMER_STATUS = {
    ReferralStatus.PENDING: "pending",
    ReferralStatus.HOLDING: "holding",
    ReferralStatus.FLAGGED: "under_review",   # reason is never shown to customers
    ReferralStatus.PAID: "paid",
    ReferralStatus.CANCELLED: "cancelled",
}


def _label(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0].upper()}."
    return parts[0] if parts else "Friend"


def _f(x):
    return float(x) if x is not None else None


@router.get("/me", response_model=ReferralSummaryOut)
def my_referrals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settle_due(db, referrer_id=current_user.id)
    code = ensure_referral_code(db, current_user)
    db.commit()

    rows = (
        db.query(Referral, User)
        .join(User, User.id == Referral.referee_id)
        .filter(Referral.referrer_id == current_user.id)
        .order_by(Referral.created_at.desc())
        .all()
    )
    items = [
        ReferralItemOut(
            id=r.id, referee_label=_label(u.full_name), status=_CUSTOMER_STATUS[r.status],
            kyc_done=r.kyc_completed_at is not None, first_txn_done=r.first_txn_at is not None,
            reward_amount_ngn=_f(r.reward_amount_ngn), release_at=r.release_at, paid_at=r.paid_at,
            created_at=r.created_at,
        )
        for r, u in rows
    ]
    return ReferralSummaryOut(
        code=code,
        reward_amount_ngn=float(REFERRAL_REWARD_NGN),
        hold_days=HOLD_DAYS,
        total_referred=len(rows),
        paid_count=sum(1 for r, _ in rows if r.status == ReferralStatus.PAID),
        paid_total_ngn=sum(float(r.reward_amount_ngn) for r, _ in rows if r.status == ReferralStatus.PAID),
        pending_reward_ngn=sum(float(r.reward_amount_ngn) for r, _ in rows if r.status == ReferralStatus.HOLDING),
        under_review_count=sum(1 for r, _ in rows if r.status == ReferralStatus.FLAGGED),
        referrals=items,
    )


# ---- Admin -------------------------------------------------------------

def _admin_out(r: Referral, referrer: User, referee: User) -> AdminReferralOut:
    return AdminReferralOut(
        id=r.id, referrer_id=referrer.id, referrer_email=referrer.email, referrer_name=referrer.full_name,
        referee_id=referee.id, referee_email=referee.email, referee_name=referee.full_name,
        status=r.status, kyc_done=r.kyc_completed_at is not None, first_txn_done=r.first_txn_at is not None,
        reward_amount_ngn=_f(r.reward_amount_ngn), flag_reason=r.flag_reason, cancel_reason=r.cancel_reason,
        release_at=r.release_at, paid_at=r.paid_at, created_at=r.created_at,
    )


def _load(db: Session, ref: Referral) -> AdminReferralOut:
    referrer = db.query(User).filter(User.id == ref.referrer_id).first()
    referee = db.query(User).filter(User.id == ref.referee_id).first()
    return _admin_out(ref, referrer, referee)


@router.get("/admin", response_model=list[AdminReferralOut])
def admin_list(
    status_filter: str | None = Query(default=None, alias="status"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if status_filter and status_filter not in ReferralStatus.ALL:
        raise HTTPException(status_code=422, detail=f"status must be one of {ReferralStatus.ALL}")
    q = db.query(Referral)
    if status_filter:
        q = q.filter(Referral.status == status_filter)
    return [_load(db, r) for r in q.order_by(Referral.created_at.desc()).limit(500).all()]


@router.post("/admin/settle", response_model=SettleResult)
def admin_settle(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    paid = settle_due(db)
    if paid:
        log_admin_action(
            db, current_admin.id, action="referral.settled", target_type="referral",
            summary=f"Paid {paid} due referral reward(s)",
        )
        db.commit()
    return SettleResult(paid=paid)


@router.post("/admin/{referral_id}/review", response_model=AdminReferralOut)
def admin_review(
    referral_id: uuid.UUID,
    payload: AdminReferralReview,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    ref = db.query(Referral).filter(Referral.id == referral_id).with_for_update().first()
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referral not found")

    if payload.action == "approve":
        if ref.status != ReferralStatus.FLAGGED:
            raise HTTPException(status_code=400, detail="Only flagged referrals can be approved")
        # Hold is measured from qualification, not from the review, so an old flag can release right away.
        ref.status = ReferralStatus.HOLDING
        ref.release_at = max(datetime.now(timezone.utc), ref.qualified_at + timedelta(days=HOLD_DAYS))
        summary = f"Approved flagged referral (was: {ref.flag_reason})"
        action = "referral.approved"
    else:
        if ref.status in (ReferralStatus.PAID, ReferralStatus.CANCELLED):
            raise HTTPException(status_code=400, detail=f"A {ref.status} referral can't be cancelled")
        if not payload.reason:
            raise HTTPException(status_code=400, detail="reason is required when cancelling")
        ref.status = ReferralStatus.CANCELLED
        ref.cancel_reason = payload.reason
        summary = f"Cancelled referral ({payload.reason})"
        action = "referral.cancelled"

    log_admin_action(
        db, current_admin.id, action=action, target_type="referral", target_id=ref.id,
        target_user_id=ref.referrer_id, summary=summary,
    )
    db.commit()
    db.refresh(ref)
    return _load(db, ref)
