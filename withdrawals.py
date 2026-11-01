"""
Withdrawal routes (crypto only — BTC/USDT; NGN withdrawal is not yet
specified/built, see roadmap).

  POST /withdrawals/crypto/preview             - see the fee breakdown WITHOUT requesting it
  POST /withdrawals/crypto                     - request a withdrawal (freezes the full amount)
  GET  /withdrawals/crypto/me                  - my withdrawal history
  GET  /withdrawals/crypto/admin/pending
  GET  /withdrawals/crypto/admin/approved       - approved but not yet completed (awaiting TxID)
  POST /withdrawals/crypto/admin/{id}/review   - approve or reject
  POST /withdrawals/crypto/admin/{id}/complete - record the TxID once admin has sent the crypto
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.models.exchange_rate import ExchangeRate
from app.models.notification import NotificationType
from app.models.transaction import TransactionType
from app.models.user import User
from app.models.withdrawal import CryptoWithdrawalRequest, WithdrawalStatus
from app.models.ngn_withdrawal import NGNWithdrawalRequest
from app.schemas.deposit_withdrawal import (
    CryptoWithdrawalOut,
    CryptoWithdrawalPreview,
    CryptoWithdrawalSubmit,
    NGNWithdrawalOut,
    NGNWithdrawalPreview,
    NGNWithdrawalReviewRequest,
    NGNWithdrawalSubmit,
    WithdrawalCompleteRequest,
    WithdrawalReviewRequest,
)
from app.utils.audit import log_admin_action
from app.utils.notification_service import notify
from app.utils.wallet_service import debit_wallet, freeze_amount, get_wallet, unfreeze_amount

router = APIRouter()


def _get_withdrawal_fees(db: Session, currency: str) -> tuple[Decimal, Decimal]:
    """Returns (network_fee, service_fee) from the admin-configured rate row. Defaults to (0, 0) if none is set yet."""
    rate = db.query(ExchangeRate).filter(ExchangeRate.currency == currency).first()
    if rate is None:
        # Decimal("0") alone doesn't carry 8-decimal-place precision the
        # way a value pulled from the NUMERIC(24,8) column does, so it
        # would serialize as "0" instead of "0.00000000" — inconsistent
        # with every other amount in the response. Use a properly-scaled
        # zero literal instead.
        zero = Decimal("0.00000000")
        return zero, zero
    return rate.withdrawal_network_fee, rate.withdrawal_service_fee


def _calculate_withdrawal(currency: str, amount: Decimal, network_fee: Decimal, service_fee: Decimal) -> dict:
    """Pure calculation, no DB writes — used for both preview and actual placement, guaranteeing they match exactly."""
    total_fees = network_fee + service_fee
    net_amount = amount - total_fees
    if net_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal amount must be greater than the total fees ({total_fees} {currency})",
        )
    return {
        "currency": currency,
        "amount": amount,
        "network_fee_amount": network_fee,
        "service_fee_amount": service_fee,
        "total_fees": total_fees,
        "net_amount": net_amount,
    }


@router.post("/crypto/preview", response_model=CryptoWithdrawalPreview)
def preview_crypto_withdrawal(
    payload: CryptoWithdrawalSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = get_wallet(db, current_user.id, payload.currency)
    network_fee, service_fee = _get_withdrawal_fees(db, payload.currency)
    calc = _calculate_withdrawal(payload.currency, payload.amount, network_fee, service_fee)

    return CryptoWithdrawalPreview(
        wallet_balance=wallet.available_balance,
        destination_address=payload.destination_address,
        **calc,
    )


@router.post("/crypto", response_model=CryptoWithdrawalOut, status_code=status.HTTP_201_CREATED)
def request_crypto_withdrawal(
    payload: CryptoWithdrawalSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = get_wallet(db, current_user.id, payload.currency)

    # Fees are locked in NOW, from whatever the admin's config is at
    # request time — not re-read later at completion (same principle
    # as Phase 5's rate_applied being locked in at order placement).
    network_fee, service_fee = _get_withdrawal_fees(db, payload.currency)
    calc = _calculate_withdrawal(payload.currency, payload.amount, network_fee, service_fee)

    freeze_amount(db, wallet, payload.amount)  # freezes the FULL amount, same freeze-then-review pattern as Phase 5 orders

    withdrawal = CryptoWithdrawalRequest(
        user_id=current_user.id,
        currency=payload.currency,
        amount=payload.amount,
        network_fee_amount=network_fee,
        service_fee_amount=service_fee,
        net_amount=calc["net_amount"],
        destination_address=payload.destination_address,
        status=WithdrawalStatus.PENDING,
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(withdrawal)
    return CryptoWithdrawalOut.model_validate(withdrawal)


@router.get("/crypto/me", response_model=list[CryptoWithdrawalOut])
def my_crypto_withdrawals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    withdrawals = (
        db.query(CryptoWithdrawalRequest)
        .filter(CryptoWithdrawalRequest.user_id == current_user.id)
        .order_by(CryptoWithdrawalRequest.created_at.desc())
        .all()
    )
    return [CryptoWithdrawalOut.model_validate(w) for w in withdrawals]


@router.get("/crypto/admin/pending", response_model=list[CryptoWithdrawalOut])
def list_pending_withdrawals(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    withdrawals = (
        db.query(CryptoWithdrawalRequest)
        .filter(CryptoWithdrawalRequest.status == WithdrawalStatus.PENDING)
        .order_by(CryptoWithdrawalRequest.created_at.asc())
        .all()
    )
    return [CryptoWithdrawalOut.model_validate(w) for w in withdrawals]


@router.get("/crypto/admin/approved", response_model=list[CryptoWithdrawalOut])
def list_approved_withdrawals(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Approved-but-not-yet-completed withdrawals -- i.e. the admin has signed
    off but hasn't actually sent the crypto and recorded a TxID yet. Without
    a UI surface for this queue, an approved withdrawal has no way to ever
    reach 'complete' and the frozen funds stay frozen forever."""
    withdrawals = (
        db.query(CryptoWithdrawalRequest)
        .filter(CryptoWithdrawalRequest.status == WithdrawalStatus.APPROVED)
        .order_by(CryptoWithdrawalRequest.reviewed_at.asc())
        .all()
    )
    return [CryptoWithdrawalOut.model_validate(w) for w in withdrawals]


@router.post("/crypto/admin/{withdrawal_id}/review", response_model=CryptoWithdrawalOut)
def review_withdrawal(
    withdrawal_id: uuid.UUID,
    payload: WithdrawalReviewRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    withdrawal = db.query(CryptoWithdrawalRequest).filter(CryptoWithdrawalRequest.id == withdrawal_id).with_for_update().populate_existing().first()
    if withdrawal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found")
    if withdrawal.status != WithdrawalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This withdrawal has already been reviewed")

    if payload.approve:
        # Approval does NOT touch the wallet yet — the crypto hasn't
        # actually been sent. The frozen amount is only debited once
        # admin completes the withdrawal with a real TxID (below).
        # This mirrors reality: approving a request isn't the same as
        # money having left the building.
        withdrawal.status = WithdrawalStatus.APPROVED
        notify(
            db, withdrawal.user_id, NotificationType.WITHDRAWAL_APPROVED,
            "Withdrawal approved", f"Your {withdrawal.currency} withdrawal request has been approved and is being processed.",
            related_id=withdrawal.id,
        )
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rejection_reason is required when rejecting")
        if not payload.rejection_action:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_action ('unfreeze' or 'keep_frozen') is required when rejecting",
            )

        if payload.rejection_action == "unfreeze":
            wallet = get_wallet(db, withdrawal.user_id, withdrawal.currency)
            unfreeze_amount(db, wallet, withdrawal.amount)

        withdrawal.status = WithdrawalStatus.REJECTED
        withdrawal.rejection_reason = payload.rejection_reason
        withdrawal.rejection_action = payload.rejection_action
        notify(
            db, withdrawal.user_id, NotificationType.WITHDRAWAL_REJECTED,
            "Withdrawal rejected", f"Your {withdrawal.currency} withdrawal was rejected: {payload.rejection_reason}",
            related_id=withdrawal.id,
        )

    withdrawal.reviewed_by = current_admin.id
    withdrawal.reviewed_at = datetime.now(timezone.utc)

    log_admin_action(
        db, current_admin.id,
        action="withdrawal.approved" if payload.approve else "withdrawal.rejected",
        target_type="crypto_withdrawal", target_id=withdrawal.id, target_user_id=withdrawal.user_id,
        summary=f"{'Approved' if payload.approve else 'Rejected'} {withdrawal.amount} {withdrawal.currency} withdrawal request"
        + (f" ({payload.rejection_reason}, {payload.rejection_action})" if not payload.approve else ""),
    )

    db.commit()
    db.refresh(withdrawal)
    return CryptoWithdrawalOut.model_validate(withdrawal)


@router.post("/crypto/admin/{withdrawal_id}/complete", response_model=CryptoWithdrawalOut)
def complete_withdrawal(
    withdrawal_id: uuid.UUID,
    payload: WithdrawalCompleteRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Called once the admin has actually sent the crypto externally and has the TxID."""
    withdrawal = db.query(CryptoWithdrawalRequest).filter(CryptoWithdrawalRequest.id == withdrawal_id).with_for_update().populate_existing().first()
    if withdrawal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found")
    if withdrawal.status != WithdrawalStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Withdrawal must be approved before it can be completed",
        )

    wallet = get_wallet(db, withdrawal.user_id, withdrawal.currency)
    debit_wallet(
        db, wallet, withdrawal.amount, tx_type=TransactionType.WITHDRAWAL,
        reference=payload.admin_tx_hash,
        description=f"Crypto withdrawal to {withdrawal.destination_address}",
        fee_amount=withdrawal.network_fee_amount + withdrawal.service_fee_amount,
        from_frozen=True, created_by_admin_id=current_admin.id,
    )

    withdrawal.status = WithdrawalStatus.COMPLETED
    withdrawal.admin_tx_hash = payload.admin_tx_hash
    withdrawal.completed_at = datetime.now(timezone.utc)
    notify(
        db, withdrawal.user_id, NotificationType.WITHDRAWAL_COMPLETED,
        "Withdrawal completed",
        f"Your {withdrawal.currency} withdrawal has been sent. You received {withdrawal.net_amount} {withdrawal.currency} "
        f"after fees. TxID: {payload.admin_tx_hash}",
        related_id=withdrawal.id,
    )
    log_admin_action(
        db, current_admin.id,
        action="withdrawal.completed", target_type="crypto_withdrawal", target_id=withdrawal.id, target_user_id=withdrawal.user_id,
        summary=f"Completed {withdrawal.net_amount} {withdrawal.currency} withdrawal — TxID {payload.admin_tx_hash}",
        details={"amount": float(withdrawal.net_amount), "currency": withdrawal.currency, "tx_hash": payload.admin_tx_hash},
    )

    db.commit()
    db.refresh(withdrawal)
    return CryptoWithdrawalOut.model_validate(withdrawal)


# --- NGN withdrawals ---
# Always pays out to the user's own registered bank account (collected at
# signup) — see app/models/ngn_withdrawal.py for why there's no separate
# "completed" step the way crypto withdrawals have one.

@router.post("/ngn/preview", response_model=NGNWithdrawalPreview)
def preview_ngn_withdrawal(
    payload: NGNWithdrawalSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = get_wallet(db, current_user.id, "NGN")
    zero = Decimal("0.00000000")
    return NGNWithdrawalPreview(
        wallet_balance=wallet.available_balance,
        amount=payload.amount,
        service_fee_amount=zero,
        net_amount=payload.amount,
        destination_bank_name=current_user.bank_name or "",
        destination_account_number=current_user.account_number or "",
        destination_account_name=current_user.account_name or "",
    )


@router.post("/ngn", response_model=NGNWithdrawalOut, status_code=status.HTTP_201_CREATED)
def request_ngn_withdrawal(
    payload: NGNWithdrawalSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not (current_user.bank_name and current_user.account_number and current_user.account_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No bank account is on file for your account. Please contact support.",
        )

    wallet = get_wallet(db, current_user.id, "NGN")
    freeze_amount(db, wallet, payload.amount)  # freezes the FULL amount, same pattern as crypto withdrawals

    withdrawal = NGNWithdrawalRequest(
        user_id=current_user.id,
        amount=payload.amount,
        service_fee_amount=Decimal("0"),
        net_amount=payload.amount,
        destination_bank_name=current_user.bank_name,
        destination_account_number=current_user.account_number,
        destination_account_name=current_user.account_name,
        status=WithdrawalStatus.PENDING,
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(withdrawal)
    return NGNWithdrawalOut.model_validate(withdrawal)


@router.get("/ngn/me", response_model=list[NGNWithdrawalOut])
def my_ngn_withdrawals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    withdrawals = (
        db.query(NGNWithdrawalRequest)
        .filter(NGNWithdrawalRequest.user_id == current_user.id)
        .order_by(NGNWithdrawalRequest.created_at.desc())
        .all()
    )
    return [NGNWithdrawalOut.model_validate(w) for w in withdrawals]


@router.get("/ngn/admin/pending", response_model=list[NGNWithdrawalOut])
def list_pending_ngn_withdrawals(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    withdrawals = (
        db.query(NGNWithdrawalRequest)
        .filter(NGNWithdrawalRequest.status == WithdrawalStatus.PENDING)
        .order_by(NGNWithdrawalRequest.created_at.asc())
        .all()
    )
    return [NGNWithdrawalOut.model_validate(w) for w in withdrawals]


@router.post("/ngn/admin/{withdrawal_id}/review", response_model=NGNWithdrawalOut)
def review_ngn_withdrawal(
    withdrawal_id: uuid.UUID,
    payload: NGNWithdrawalReviewRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    withdrawal = db.query(NGNWithdrawalRequest).filter(NGNWithdrawalRequest.id == withdrawal_id).with_for_update().populate_existing().first()
    if withdrawal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found")
    if withdrawal.status != WithdrawalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This withdrawal has already been reviewed")

    if payload.approve:
        # Unlike crypto withdrawals, there's no separate manual-send step
        # to wait on here — approving a bank transfer withdrawal finalizes
        # the debit immediately (see app/models/ngn_withdrawal.py).
        wallet = get_wallet(db, withdrawal.user_id, "NGN")
        debit_wallet(
            db, wallet, withdrawal.amount, tx_type=TransactionType.WITHDRAWAL,
            reference=payload.admin_reference,
            description=f"NGN withdrawal to {withdrawal.destination_bank_name} \u2022\u2022\u2022{withdrawal.destination_account_number[-4:]}",
            fee_amount=withdrawal.service_fee_amount,
            from_frozen=True, created_by_admin_id=current_admin.id,
        )
        withdrawal.status = WithdrawalStatus.COMPLETED
        withdrawal.admin_reference = payload.admin_reference
        withdrawal.completed_at = datetime.now(timezone.utc)
        notify(
            db, withdrawal.user_id, NotificationType.WITHDRAWAL_COMPLETED,
            "Withdrawal completed",
            f"Your \u20a6{withdrawal.net_amount} withdrawal to {withdrawal.destination_bank_name} "
            f"\u2022\u2022\u2022{withdrawal.destination_account_number[-4:]} has been sent.",
            related_id=withdrawal.id,
        )
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rejection_reason is required when rejecting")
        if not payload.rejection_action:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rejection_action ('unfreeze' or 'keep_frozen') is required when rejecting",
            )

        if payload.rejection_action == "unfreeze":
            wallet = get_wallet(db, withdrawal.user_id, "NGN")
            unfreeze_amount(db, wallet, withdrawal.amount)

        withdrawal.status = WithdrawalStatus.REJECTED
        withdrawal.rejection_reason = payload.rejection_reason
        withdrawal.rejection_action = payload.rejection_action
        notify(
            db, withdrawal.user_id, NotificationType.WITHDRAWAL_REJECTED,
            "Withdrawal rejected", f"Your \u20a6{withdrawal.amount} NGN withdrawal was rejected: {payload.rejection_reason}",
            related_id=withdrawal.id,
        )

    withdrawal.reviewed_by = current_admin.id
    withdrawal.reviewed_at = datetime.now(timezone.utc)

    log_admin_action(
        db, current_admin.id,
        action="withdrawal.completed" if payload.approve else "withdrawal.rejected",
        target_type="ngn_withdrawal", target_id=withdrawal.id, target_user_id=withdrawal.user_id,
        summary=(
            f"Completed \u20a6{withdrawal.net_amount} NGN withdrawal to {withdrawal.destination_bank_name} "
            f"\u2022\u2022\u2022{withdrawal.destination_account_number[-4:]} \u2014 ref {payload.admin_reference}"
        ) if payload.approve else (
            f"Rejected \u20a6{withdrawal.amount} NGN withdrawal request ({payload.rejection_reason}, {payload.rejection_action})"
        ),
        details={"amount": float(withdrawal.amount), "reference": payload.admin_reference} if payload.approve else None,
    )

    db.commit()
    db.refresh(withdrawal)
    return NGNWithdrawalOut.model_validate(withdrawal)
