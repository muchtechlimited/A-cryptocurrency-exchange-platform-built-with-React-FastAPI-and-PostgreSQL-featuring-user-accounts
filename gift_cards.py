"""
Gift card sell-submission routes.

User-facing:
  POST /gift-cards/submit                          - sell a gift card
  GET  /gift-cards/me                               - my own submissions
  GET  /gift-cards/rates                            - current Much Exchange buy rates per brand
  GET  /gift-cards/documents/{submission_id}/card-image  - view uploaded card photo (owner or admin)

Admin-facing:
  GET  /gift-cards/admin/pending                    - review queue (filter by status)
  GET  /gift-cards/admin/stats                       - pending/approved/rejected counts + total value
  GET  /gift-cards/admin/{submission_id}             - full detail incl. audit log
  GET  /gift-cards/admin/{submission_id}/redemption-code  - reveal (audit-logged)
  POST /gift-cards/admin/{submission_id}/verify      - record vendor verification result
  POST /gift-cards/admin/{submission_id}/review      - approve or reject
  PUT  /gift-cards/admin/rates/{brand}                - set the Much Exchange buy rate for a brand
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import has_permission
from app.core.deps import get_current_admin, get_current_user, get_current_user_or_admin
from app.models.gift_card import (
    GiftCardApiWebhookStatus,
    GiftCardAuditLog,
    GiftCardPaymentMethod,
    GiftCardPurchase,
    GiftCardPurchaseStatus,
    GiftCardRate,
    GiftCardStatus,
    GiftCardStock,
    GiftCardStockStatus,
    GiftCardSubmission,
    GiftCardType,
    GiftCardVerificationResult,
)
from app.models.notification import NotificationType
from app.models.transaction import TransactionType
from app.models.user import User
from app.schemas.gift_card import (
    GiftCardAdminDetail,
    GiftCardAuditLogOut,
    GiftCardCatalogEntry,
    GiftCardOut,
    GiftCardPurchaseOut,
    GiftCardPurchaseRequest,
    GiftCardRateOut,
    GiftCardRateUpdate,
    GiftCardReviewRequest,
    GiftCardStatsOut,
    GiftCardStockAddRequest,
    GiftCardStockOut,
    GiftCardSubmitRequest,
    GiftCardVerifyRequest,
)
from app.utils.audit import log_admin_action
from app.utils.file_upload import get_private_file_path, save_upload_file
from app.utils.notification_service import notify
from app.utils.promotion_service import count_qualifying_transactions, redeem_first_trade_promotions
from app.utils.referral_service import mark_qualifying_transaction
from app.utils.wallet_service import credit_wallet, debit_wallet, get_wallet

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


def _mask_code(code: str) -> str:
    if len(code) <= 4:
        return "*" * len(code)
    return "*" * (len(code) - 4) + code[-4:]


def _log(db: Session, submission_id: uuid.UUID, actor_id: uuid.UUID | None, action: str, details: str | None = None):
    db.add(GiftCardAuditLog(submission_id=submission_id, actor_id=actor_id, action=action, details=details))


def _to_out(submission: GiftCardSubmission) -> GiftCardOut:
    out = GiftCardOut.model_validate(submission)
    out.has_card_image = submission.card_image_path is not None
    return out


def _to_admin_detail(submission: GiftCardSubmission, db: Session) -> GiftCardAdminDetail:
    user = db.query(User).filter(User.id == submission.user_id).first()
    out = GiftCardAdminDetail.model_validate(submission)
    out.has_card_image = submission.card_image_path is not None
    out.redemption_code_masked = _mask_code(submission.redemption_code)
    out.user_email = user.email if user else None
    out.user_full_name = user.full_name if user else None
    out.user_phone = user.phone_number if user else None
    return out


def _stock_to_out(item: GiftCardStock) -> GiftCardStockOut:
    out = GiftCardStockOut.model_validate(item)
    out.code_masked = _mask_code(item.code)
    return out


# --- User-facing ---

@router.post("/submit", response_model=GiftCardOut, status_code=status.HTTP_201_CREATED)
def submit_gift_card(
    brand: str,
    card_type: str,
    card_country: str,
    card_currency: str,
    card_denomination: Decimal,
    redemption_code: str,
    payment_method: str,
    card_image: UploadFile | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if card_type not in GiftCardType.ALL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid card_type")
    if payment_method not in GiftCardPaymentMethod.ALL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment_method")
    if card_denomination <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="card_denomination must be positive")

    rate = db.query(GiftCardRate).filter(GiftCardRate.brand == brand).first()
    if rate is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No rate configured for brand '{brand}' yet — an admin needs to set one first.",
        )

    image_path = None
    if card_image is not None:
        image_path = save_upload_file(card_image, subfolder=f"gift-cards/{current_user.id}", private=True)

    expected_payout = (card_denomination * rate.buy_rate_ngn).quantize(Decimal("0.00000001"))

    submission = GiftCardSubmission(
        user_id=current_user.id,
        brand=brand,
        card_type=card_type,
        card_country=card_country,
        card_currency=card_currency,
        card_denomination=card_denomination,
        redemption_code=redemption_code,
        card_image_path=image_path,
        payment_method=payment_method,
        much_exchange_rate=rate.buy_rate_ngn,
        expected_payout=expected_payout,
        status=GiftCardStatus.PENDING,
        verification_result=GiftCardVerificationResult.PENDING,
        api_webhook_status=GiftCardApiWebhookStatus.NOT_SUBMITTED,
    )
    db.add(submission)
    db.flush()  # get submission.id before committing

    _log(db, submission.id, current_user.id, "submitted", f"{brand} {card_currency}{card_denomination} via {payment_method}")

    db.commit()
    db.refresh(submission)
    return _to_out(submission)


@router.get("/me", response_model=list[GiftCardOut])
def my_gift_card_submissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submissions = (
        db.query(GiftCardSubmission)
        .filter(GiftCardSubmission.user_id == current_user.id)
        .order_by(GiftCardSubmission.created_at.desc())
        .all()
    )
    return [_to_out(s) for s in submissions]


@router.get("/rates", response_model=list[GiftCardRateOut])
def list_gift_card_rates(
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    return [GiftCardRateOut.model_validate(r) for r in db.query(GiftCardRate).all()]


@router.get("/documents/{submission_id}/card-image")
def get_card_image(
    submission_id: uuid.UUID,
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    submission = db.query(GiftCardSubmission).filter(GiftCardSubmission.id == submission_id).first()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if submission.user_id != current_user.id and not has_permission(current_user, "giftcards", "view"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this image")
    if submission.card_image_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No card image on this submission")
    return FileResponse(get_private_file_path(submission.card_image_path))


# --- Admin-facing ---

@router.get("/admin/pending", response_model=list[GiftCardAdminDetail])
def list_gift_card_submissions(
    status_filter: str | None = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """status_filter: pending | approved | rejected. Omit to get all."""
    query = db.query(GiftCardSubmission)
    if status_filter:
        if status_filter not in GiftCardStatus.ALL:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status filter")
        query = query.filter(GiftCardSubmission.status == status_filter)
    submissions = query.order_by(GiftCardSubmission.created_at.desc()).all()
    return [_to_admin_detail(s, db) for s in submissions]


@router.get("/admin/stats", response_model=GiftCardStatsOut)
def gift_card_stats(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    submissions = db.query(GiftCardSubmission).all()
    pending = sum(1 for s in submissions if s.status == GiftCardStatus.PENDING)
    approved = sum(1 for s in submissions if s.status == GiftCardStatus.APPROVED)
    rejected = sum(1 for s in submissions if s.status == GiftCardStatus.REJECTED)
    total_value = sum((s.actual_payout or Decimal("0")) for s in submissions if s.status == GiftCardStatus.APPROVED)
    return GiftCardStatsOut(
        pending_count=pending, approved_count=approved, rejected_count=rejected,
        total_value_ngn=total_value if isinstance(total_value, Decimal) else Decimal(total_value),
    )


# --- Admin: stock management (buy side) ---
# NOTE: these literal-path routes (/admin/stock, /admin/purchases) MUST be
# registered before the generic /admin/{submission_id} route below —
# FastAPI matches routes in declaration order, so a catch-all path param
# registered first would otherwise swallow "stock" and "purchases" as a
# submission_id.

@router.post("/admin/stock", response_model=list[GiftCardStockOut], status_code=status.HTTP_201_CREATED)
def add_gift_card_stock(
    payload: GiftCardStockAddRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    items = []
    for code in payload.codes:
        item = GiftCardStock(
            brand=payload.brand,
            card_type=payload.card_type,
            card_country=payload.card_country,
            card_currency=payload.card_currency,
            card_denomination=payload.card_denomination,
            code=code,
            status=GiftCardStockStatus.AVAILABLE,
            added_by_admin_id=current_admin.id,
        )
        db.add(item)
        items.append(item)
    log_admin_action(
        db, current_admin.id,
        action="gift_card_stock.added", target_type="gift_card_stock",
        summary=f"Added {len(payload.codes)} {payload.brand} {payload.card_denomination} {payload.card_currency} code(s) to stock",
        details={"brand": payload.brand, "count": len(payload.codes), "denomination": float(payload.card_denomination), "currency": payload.card_currency},
    )
    db.commit()
    for item in items:
        db.refresh(item)
    return [_stock_to_out(i) for i in items]


@router.get("/admin/stock", response_model=list[GiftCardStockOut])
def list_gift_card_stock(
    status_filter: str | None = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    query = db.query(GiftCardStock)
    if status_filter:
        if status_filter not in GiftCardStockStatus.ALL:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status filter")
        query = query.filter(GiftCardStock.status == status_filter)
    items = query.order_by(GiftCardStock.created_at.desc()).all()
    return [_stock_to_out(i) for i in items]


@router.get("/admin/stock/{stock_id}/code")
def reveal_stock_code(
    stock_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    item = db.query(GiftCardStock).filter(GiftCardStock.id == stock_id).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock item not found")
    return {"code": item.code}


@router.delete("/admin/stock/{stock_id}", response_model=GiftCardStockOut)
def retire_gift_card_stock(
    stock_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    item = db.query(GiftCardStock).filter(GiftCardStock.id == stock_id).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock item not found")
    if item.status == GiftCardStockStatus.SOLD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can't retire a code that's already been sold")
    item.status = GiftCardStockStatus.RETIRED
    log_admin_action(
        db, current_admin.id,
        action="gift_card_stock.retired", target_type="gift_card_stock", target_id=item.id,
        summary=f"Retired a {item.brand} {item.card_denomination} {item.card_currency} stock code",
    )
    db.commit()
    db.refresh(item)
    return _stock_to_out(item)


@router.get("/admin/purchases", response_model=list[GiftCardPurchaseOut])
def list_gift_card_purchases(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Admin sales report — delivered codes are omitted here (already the buyer's property)."""
    purchases = db.query(GiftCardPurchase).order_by(GiftCardPurchase.created_at.desc()).all()
    return [GiftCardPurchaseOut.model_validate(p) for p in purchases]


@router.get("/admin/{submission_id}", response_model=GiftCardAdminDetail)
def get_gift_card_submission(
    submission_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    submission = db.query(GiftCardSubmission).filter(GiftCardSubmission.id == submission_id).first()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    return _to_admin_detail(submission, db)


@router.get("/admin/{submission_id}/audit-log", response_model=list[GiftCardAuditLogOut])
def get_gift_card_audit_log(
    submission_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    entries = (
        db.query(GiftCardAuditLog)
        .filter(GiftCardAuditLog.submission_id == submission_id)
        .order_by(GiftCardAuditLog.created_at.asc())
        .all()
    )
    results = []
    for e in entries:
        out = GiftCardAuditLogOut.model_validate(e)
        if e.actor_id:
            actor = db.query(User).filter(User.id == e.actor_id).first()
            out.actor_name = actor.full_name if actor else None
        else:
            out.actor_name = "System"
        results.append(out)
    return results


@router.get("/admin/{submission_id}/redemption-code")
def reveal_redemption_code(
    submission_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    submission = db.query(GiftCardSubmission).filter(GiftCardSubmission.id == submission_id).first()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

    _log(db, submission.id, current_admin.id, "redemption_code_viewed")
    db.commit()

    return {"redemption_code": submission.redemption_code}


@router.post("/admin/{submission_id}/verify", response_model=GiftCardAdminDetail)
def verify_gift_card_submission(
    submission_id: uuid.UUID,
    payload: GiftCardVerifyRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    submission = db.query(GiftCardSubmission).filter(GiftCardSubmission.id == submission_id).with_for_update().populate_existing().first()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if submission.status != GiftCardStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This submission has already been reviewed")
    if payload.verification_result not in GiftCardVerificationResult.ALL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification_result")

    submission.vendor = payload.vendor
    submission.vendor_transaction_id = payload.vendor_transaction_id
    submission.current_vendor_rate = payload.current_vendor_rate
    submission.verification_result = payload.verification_result
    submission.verification_notes = payload.verification_notes
    submission.api_webhook_status = payload.api_webhook_status or GiftCardApiWebhookStatus.PENDING

    _log(
        db, submission.id, current_admin.id, "verification_updated",
        f"vendor={payload.vendor} result={payload.verification_result}",
    )

    db.commit()
    db.refresh(submission)
    return _to_admin_detail(submission, db)


@router.post("/admin/{submission_id}/review", response_model=GiftCardAdminDetail)
def review_gift_card_submission(
    submission_id: uuid.UUID,
    payload: GiftCardReviewRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    submission = db.query(GiftCardSubmission).filter(GiftCardSubmission.id == submission_id).with_for_update().populate_existing().first()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if submission.status != GiftCardStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This submission has already been reviewed")
    if not payload.approve and not payload.rejection_reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rejection_reason is required when rejecting")

    user = db.query(User).filter(User.id == submission.user_id).first()
    is_first_trade = count_qualifying_transactions(db, user.id) == 0

    if payload.approve:
        payout = payload.actual_payout if payload.actual_payout is not None else submission.expected_payout
        submission.actual_payout = payout
        submission.status = GiftCardStatus.APPROVED

        # Credit the seller's NGN wallet and log a ledger transaction —
        # same "wallet_service is the only place balances change" rule
        # as every other flow in this app.
        wallet = get_wallet(db, user.id, "NGN")
        credit_wallet(
            db, wallet, payout, TransactionType.GIFT_CARD_SELL,
            reference=str(submission.id),
            description=f"{submission.brand} gift card sale ({submission.card_currency}{submission.card_denomination})",
            created_by_admin_id=current_admin.id,
        )

        notify(
            db, user.id, NotificationType.GIFT_CARD_APPROVED,
            "Gift card approved", f"Your {submission.brand} gift card was approved. ₦{payout} has been credited to your wallet.",
            related_id=submission.id,
        )
        _log(db, submission.id, current_admin.id, "approved", f"actual_payout={payout}")
        mark_qualifying_transaction(db, user.id)
        redeem_first_trade_promotions(db, user, payout, is_first_trade)
    else:
        submission.status = GiftCardStatus.REJECTED
        submission.rejection_reason = payload.rejection_reason
        if user:
            notify(
                db, user.id, NotificationType.GIFT_CARD_REJECTED,
                "Gift card rejected", f"Your {submission.brand} gift card was rejected: {payload.rejection_reason}",
                related_id=submission.id,
            )
        _log(db, submission.id, current_admin.id, "rejected", payload.rejection_reason)

    submission.reviewed_by = current_admin.id
    submission.reviewed_at = _utcnow()

    log_admin_action(
        db, current_admin.id,
        action="gift_card.approved" if payload.approve else "gift_card.rejected",
        target_type="gift_card_submission", target_id=submission.id, target_user_id=submission.user_id,
        summary=f"{'Approved' if payload.approve else 'Rejected'} {submission.brand} gift card submission"
        + (f" \u2014 \u20a6{payload.actual_payout or submission.expected_payout} paid out" if payload.approve else f" ({payload.rejection_reason})"),
    )

    db.commit()
    db.refresh(submission)
    return _to_admin_detail(submission, db)


@router.put("/admin/rates/{brand}", response_model=GiftCardRateOut)
def set_gift_card_rate(
    brand: str,
    payload: GiftCardRateUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rate = db.query(GiftCardRate).filter(GiftCardRate.brand == brand).first()
    is_new = rate is None
    if rate is None:
        rate = GiftCardRate(brand=brand, buy_rate_ngn=payload.buy_rate_ngn, sell_rate_ngn=payload.sell_rate_ngn)
        db.add(rate)
    else:
        rate.buy_rate_ngn = payload.buy_rate_ngn
        rate.sell_rate_ngn = payload.sell_rate_ngn
    rate.updated_by_admin_id = current_admin.id

    log_admin_action(
        db, current_admin.id,
        action="gift_card_rate.created" if is_new else "gift_card_rate.updated",
        target_type="gift_card_rate", target_id=brand,
        summary=f"{'Set' if is_new else 'Updated'} {brand} gift card rate \u2014 buy {payload.buy_rate_ngn} / sell {payload.sell_rate_ngn} NGN",
        details={"buy_rate_ngn": float(payload.buy_rate_ngn), "sell_rate_ngn": float(payload.sell_rate_ngn)},
    )

    db.commit()
    db.refresh(rate)
    return GiftCardRateOut.model_validate(rate)


# --- Buy side: Much Exchange sells gift cards to users ---

@router.get("/catalog", response_model=list[GiftCardCatalogEntry])
def list_gift_card_catalog(
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    """
    Every distinct (brand, card_type, country, currency, denomination)
    combo that currently has at least one available code in stock,
    priced at that brand's current sell_rate_ngn.
    """
    available = (
        db.query(GiftCardStock)
        .filter(GiftCardStock.status == GiftCardStockStatus.AVAILABLE)
        .all()
    )
    rates = {r.brand: r for r in db.query(GiftCardRate).all()}

    groups: dict[tuple, int] = {}
    for s in available:
        key = (s.brand, s.card_type, s.card_country, s.card_currency, s.card_denomination)
        groups[key] = groups.get(key, 0) + 1

    entries = []
    for (brand, card_type, country, currency, denom), qty in groups.items():
        rate = rates.get(brand)
        if rate is None or rate.sell_rate_ngn <= 0:
            continue  # no sell price configured for this brand yet — not orderable
        price = (denom * rate.sell_rate_ngn).quantize(Decimal("0.00000001"))
        entries.append(GiftCardCatalogEntry(
            brand=brand, card_type=card_type, card_country=country, card_currency=currency,
            card_denomination=denom, sell_rate_ngn=rate.sell_rate_ngn, price_ngn=price,
            quantity_available=qty,
        ))
    return entries


@router.post("/buy", response_model=GiftCardPurchaseOut, status_code=status.HTTP_201_CREATED)
def buy_gift_card(
    payload: GiftCardPurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.payment_method != GiftCardPaymentMethod.WALLET_BALANCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only wallet_balance is supported for buying gift cards right now",
        )

    rate = db.query(GiftCardRate).filter(GiftCardRate.brand == payload.brand).first()
    if rate is None or rate.sell_rate_ngn <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{payload.brand}' is not currently available to buy")

    # Computed before any writes this request makes, so it can't count itself.
    is_first_trade = count_qualifying_transactions(db, current_user.id) == 0

    # Lock the row so two concurrent buyers can't both claim the same code.
    stock_item = (
        db.query(GiftCardStock)
        .filter(
            GiftCardStock.brand == payload.brand,
            GiftCardStock.card_type == payload.card_type,
            GiftCardStock.card_country == payload.card_country,
            GiftCardStock.card_currency == payload.card_currency,
            GiftCardStock.card_denomination == payload.card_denomination,
            GiftCardStock.status == GiftCardStockStatus.AVAILABLE,
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if stock_item is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Out of stock for that brand/denomination right now")

    total_cost = (payload.card_denomination * rate.sell_rate_ngn).quantize(Decimal("0.00000001"))

    wallet = get_wallet(db, current_user.id, "NGN")
    debit_wallet(
        db, wallet, total_cost, TransactionType.BUY_GIFT_CARD,
        description=f"Bought {payload.brand} gift card ({payload.card_currency}{payload.card_denomination})",
    )

    purchase = GiftCardPurchase(
        user_id=current_user.id,
        stock_id=stock_item.id,
        brand=payload.brand,
        card_type=payload.card_type,
        card_country=payload.card_country,
        card_currency=payload.card_currency,
        card_denomination=payload.card_denomination,
        sell_rate_ngn=rate.sell_rate_ngn,
        total_cost_ngn=total_cost,
        payment_method=payload.payment_method,
        status=GiftCardPurchaseStatus.COMPLETED,
    )
    db.add(purchase)
    db.flush()  # get purchase.id

    stock_item.status = GiftCardStockStatus.SOLD
    stock_item.sold_in_purchase_id = purchase.id

    mark_qualifying_transaction(db, current_user.id)
    redeem_first_trade_promotions(db, current_user, total_cost, is_first_trade)
    db.commit()
    db.refresh(purchase)

    out = GiftCardPurchaseOut.model_validate(purchase)
    out.delivered_code = stock_item.code  # only ever shown here, right after purchase, and in /purchases/me for the buyer
    return out


@router.get("/purchases/me", response_model=list[GiftCardPurchaseOut])
def my_gift_card_purchases(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    purchases = (
        db.query(GiftCardPurchase)
        .filter(GiftCardPurchase.user_id == current_user.id)
        .order_by(GiftCardPurchase.created_at.desc())
        .all()
    )
    results = []
    for p in purchases:
        out = GiftCardPurchaseOut.model_validate(p)
        stock_item = db.query(GiftCardStock).filter(GiftCardStock.id == p.stock_id).first()
        out.delivered_code = stock_item.code if stock_item else None
        results.append(out)
    return results
