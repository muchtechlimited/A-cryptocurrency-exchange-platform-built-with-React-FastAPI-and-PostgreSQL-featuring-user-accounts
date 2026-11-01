"""
Promotion routes.

  GET  /promotions/active            - public list of currently-running promotions (no secret codes)
  GET  /promotions/me                - my redemptions (also settles anything due for me)
  GET  /promotions/admin             - all campaigns, with redemption stats
  POST /promotions/admin             - create a campaign
  PUT  /promotions/admin/{id}        - pause/resume, retitle, adjust cap or date window
  GET  /promotions/admin/{id}/redemptions - who has redeemed this campaign
  POST /promotions/admin/settle      - pay every due reward now (cron-friendly)

Rules live in app/utils/promotion_service.py.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.models.promotion import Promotion, PromotionRedemption, PromotionRedemptionStatus
from app.models.user import User
from app.schemas.promotion import (
    AdminRedemptionOut,
    MyPromotionsOut,
    PromotionAdminOut,
    PromotionCreate,
    PromotionOut,
    PromotionRedemptionOut,
    PromotionUpdate,
    SettleResult,
)
from app.utils.audit import log_admin_action
from app.utils.promotion_service import settle_due

router = APIRouter()


def _public_out(p: Promotion) -> PromotionOut:
    return PromotionOut(
        id=p.id, name=p.name, description=p.description, has_code=p.code is not None,
        trigger=p.trigger, reward_type=p.reward_type, reward_value=float(p.reward_value),
        reward_cap_ngn=float(p.reward_cap_ngn) if p.reward_cap_ngn is not None else None,
        min_amount_ngn=float(p.min_amount_ngn) if p.min_amount_ngn is not None else None,
        hold_days=p.hold_days, stackable=p.stackable, requires_kyc=p.requires_kyc,
        max_redemptions_total=p.max_redemptions_total, starts_at=p.starts_at, ends_at=p.ends_at,
        is_active=p.is_active, created_at=p.created_at,
    )


@router.get("/active", response_model=list[PromotionOut])
def active_promotions(db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    q = db.query(Promotion).filter(Promotion.is_active.is_(True))
    q = q.filter((Promotion.starts_at.is_(None)) | (Promotion.starts_at <= now))
    q = q.filter((Promotion.ends_at.is_(None)) | (Promotion.ends_at >= now))
    return [_public_out(p) for p in q.order_by(Promotion.created_at.desc()).all()]


@router.get("/me", response_model=MyPromotionsOut)
def my_promotions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settle_due(db, user_id=current_user.id)
    rows = (
        db.query(PromotionRedemption, Promotion)
        .join(Promotion, Promotion.id == PromotionRedemption.promotion_id)
        .filter(PromotionRedemption.user_id == current_user.id)
        .order_by(PromotionRedemption.created_at.desc())
        .all()
    )
    items = [
        PromotionRedemptionOut(
            id=r.id, promotion_id=r.promotion_id, promotion_name=p.name, status=r.status,
            reward_amount_ngn=float(r.reward_amount_ngn),
            qualifying_amount_ngn=float(r.qualifying_amount_ngn) if r.qualifying_amount_ngn is not None else None,
            release_at=r.release_at, paid_at=r.paid_at, cancel_reason=r.cancel_reason, created_at=r.created_at,
        )
        for r, p in rows
    ]
    return MyPromotionsOut(
        redemptions=items,
        pending_reward_ngn=sum(i.reward_amount_ngn for i in items if i.status == "holding"),
        paid_total_ngn=sum(i.reward_amount_ngn for i in items if i.status == "paid"),
    )


# ---- Admin -------------------------------------------------------------

def _admin_out(db: Session, p: Promotion) -> PromotionAdminOut:
    rows = db.query(PromotionRedemption).filter(PromotionRedemption.promotion_id == p.id).all()
    paid = [r for r in rows if r.status == PromotionRedemptionStatus.PAID]
    base = _public_out(p).model_dump()
    return PromotionAdminOut(
        **base, code=p.code, redemption_count=len(rows), paid_count=len(paid),
        paid_total_ngn=sum(float(r.reward_amount_ngn) for r in paid),
    )


@router.get("/admin", response_model=list[PromotionAdminOut])
def admin_list(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    promos = db.query(Promotion).order_by(Promotion.created_at.desc()).all()
    return [_admin_out(db, p) for p in promos]


@router.post("/admin", response_model=PromotionAdminOut, status_code=status.HTTP_201_CREATED)
def admin_create(
    payload: PromotionCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if payload.code:
        if db.query(Promotion.id).filter(Promotion.code == payload.code).first():
            raise HTTPException(status_code=400, detail="That code is already in use by another promotion")
    promo = Promotion(
        name=payload.name, description=payload.description, code=payload.code, trigger=payload.trigger,
        reward_type=payload.reward_type, reward_value=payload.reward_value, reward_cap_ngn=payload.reward_cap_ngn,
        min_amount_ngn=payload.min_amount_ngn, hold_days=payload.hold_days, stackable=payload.stackable,
        requires_kyc=payload.requires_kyc, max_redemptions_total=payload.max_redemptions_total,
        starts_at=payload.starts_at, ends_at=payload.ends_at, created_by_admin_id=current_admin.id,
    )
    db.add(promo)
    log_admin_action(
        db, current_admin.id, action="promotion.created", target_type="promotion",
        summary=f"Created promotion '{payload.name}' ({payload.trigger}, "
        f"{'stackable' if payload.stackable else 'NOT stackable'})",
    )
    db.commit()
    db.refresh(promo)
    return _admin_out(db, promo)


@router.put("/admin/{promotion_id}", response_model=PromotionAdminOut)
def admin_update(
    promotion_id: uuid.UUID,
    payload: PromotionUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if promo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    if payload.starts_at and payload.ends_at and payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")
    elif payload.starts_at and not payload.ends_at and promo.ends_at and promo.ends_at <= payload.starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")
    elif payload.ends_at and not payload.starts_at and promo.starts_at and payload.ends_at <= promo.starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")

    changes = []
    for field in ("name", "description", "is_active", "max_redemptions_total", "starts_at", "ends_at"):
        value = getattr(payload, field)
        if value is not None:
            setattr(promo, field, value)
            changes.append(field)

    log_admin_action(
        db, current_admin.id, action="promotion.updated", target_type="promotion", target_id=promo.id,
        summary=f"Updated promotion '{promo.name}': {', '.join(changes) if changes else 'no fields changed'}",
    )
    db.commit()
    db.refresh(promo)
    return _admin_out(db, promo)


@router.get("/admin/{promotion_id}/redemptions", response_model=list[AdminRedemptionOut])
def admin_redemptions(
    promotion_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if promo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    rows = (
        db.query(PromotionRedemption, User)
        .join(User, User.id == PromotionRedemption.user_id)
        .filter(PromotionRedemption.promotion_id == promotion_id)
        .order_by(PromotionRedemption.created_at.desc())
        .all()
    )
    return [
        AdminRedemptionOut(
            id=r.id, promotion_id=r.promotion_id, promotion_name=promo.name, status=r.status,
            reward_amount_ngn=float(r.reward_amount_ngn),
            qualifying_amount_ngn=float(r.qualifying_amount_ngn) if r.qualifying_amount_ngn is not None else None,
            release_at=r.release_at, paid_at=r.paid_at, cancel_reason=r.cancel_reason, created_at=r.created_at,
            user_id=u.id, user_email=u.email, user_name=u.full_name,
        )
        for r, u in rows
    ]


@router.post("/admin/settle", response_model=SettleResult)
def admin_settle(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    paid = settle_due(db)
    if paid:
        log_admin_action(
            db, current_admin.id, action="promotion.settled", target_type="promotion",
            summary=f"Paid {paid} due promotion reward(s)",
        )
        db.commit()
    return SettleResult(paid=paid)
