"""
Deposit routes.

Crypto (BTC/USDT) — manually reviewed:
  POST /deposits/crypto              - submit amount + TxID after sending externally
  GET  /deposits/crypto/me           - my crypto deposit history
  GET  /deposits/crypto/admin/pending
  POST /deposits/crypto/admin/{id}/review

NGN via Paystack — automatically verified:
  POST /deposits/ngn/initialize      - start a Paystack payment, get a checkout URL
  GET  /deposits/ngn/verify/{reference}  - manually trigger verification (fallback if webhook is delayed/missed)
  POST /deposits/ngn/webhook         - Paystack calls this when payment completes

NGN via direct bank transfer — manually reviewed, reference number
generated UPFRONT (before the user sends anything):
  POST /deposits/ngn/bank-transfer/initiate       - get a reference number + company bank details + warning
  GET  /deposits/ngn/bank-transfer/me             - my bank transfer deposit history
  GET  /deposits/ngn/bank-transfer/admin/pending
  POST /deposits/ngn/bank-transfer/admin/{id}/review
"""

import uuid
from datetime import datetime, timezone

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.core.rate_limit import limiter
from app.models.deposit import CryptoDepositRequest, DepositStatus
from app.models.ngn_bank_transfer import NGNBankTransferDeposit, NGNBankTransferStatus, generate_reference_number
from app.models.ngn_deposit import NGNDepositRequest, NGNDepositStatus
from app.models.notification import NotificationType
from app.models.transaction import TransactionType
from app.models.user import User
from app.schemas.deposit_withdrawal import (
    CryptoDepositOut,
    CryptoDepositSubmit,
    DepositReviewRequest,
    NGNBankTransferInitiate,
    NGNBankTransferInitiateResponse,
    NGNBankTransferOut,
    NGNBankTransferReviewRequest,
    NGNDepositInitRequest,
    NGNDepositInitResponse,
    NGNDepositOut,
)
from app.models.company_bank_account import CompanyBankAccount
from app.utils.paystack_service import initialize_transaction, verify_transaction
from app.utils.audit import log_admin_action
from app.utils.notification_service import notify
from app.utils.promotion_service import count_completed_ngn_deposits, redeem_deposit_promotions
from app.utils.wallet_service import credit_wallet, get_wallet

router = APIRouter()


# --- Crypto deposits ---

@router.post("/crypto", response_model=CryptoDepositOut, status_code=status.HTTP_201_CREATED)
def submit_crypto_deposit(
    payload: CryptoDepositSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deposit = CryptoDepositRequest(
        user_id=current_user.id,
        currency=payload.currency,
        amount=payload.amount,
        tx_hash=payload.tx_hash,
    )
    db.add(deposit)
    db.commit()
    db.refresh(deposit)
    return CryptoDepositOut.model_validate(deposit)


@router.get("/crypto/me", response_model=list[CryptoDepositOut])
def my_crypto_deposits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deposits = (
        db.query(CryptoDepositRequest)
        .filter(CryptoDepositRequest.user_id == current_user.id)
        .order_by(CryptoDepositRequest.created_at.desc())
        .all()
    )
    return [CryptoDepositOut.model_validate(d) for d in deposits]


@router.get("/crypto/admin/pending", response_model=list[CryptoDepositOut])
def list_pending_crypto_deposits(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    deposits = (
        db.query(CryptoDepositRequest)
        .filter(CryptoDepositRequest.status == DepositStatus.PENDING)
        .order_by(CryptoDepositRequest.created_at.asc())
        .all()
    )
    return [CryptoDepositOut.model_validate(d) for d in deposits]


@router.post("/crypto/admin/{deposit_id}/review", response_model=CryptoDepositOut)
def review_crypto_deposit(
    deposit_id: uuid.UUID,
    payload: DepositReviewRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timezone

    deposit = db.query(CryptoDepositRequest).filter(CryptoDepositRequest.id == deposit_id).with_for_update().populate_existing().first()
    if deposit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deposit request not found")
    if deposit.status != DepositStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This deposit has already been reviewed")

    if payload.approve:
        wallet = get_wallet(db, deposit.user_id, deposit.currency)
        credit_wallet(
            db, wallet, deposit.amount, tx_type=TransactionType.DEPOSIT,
            reference=deposit.tx_hash,
            description=f"Crypto deposit approved: {deposit.currency} {deposit.amount}",
            created_by_admin_id=current_admin.id,
        )
        deposit.status = DepositStatus.APPROVED
        notify(
            db, deposit.user_id, NotificationType.DEPOSIT_APPROVED,
            "Deposit approved", f"Your {deposit.currency} deposit of {deposit.amount} has been approved and credited.",
            related_id=deposit.id,
        )
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rejection_reason is required when rejecting")
        deposit.status = DepositStatus.REJECTED
        deposit.rejection_reason = payload.rejection_reason
        notify(
            db, deposit.user_id, NotificationType.DEPOSIT_REJECTED,
            "Deposit rejected", f"Your {deposit.currency} deposit was rejected: {payload.rejection_reason}",
            related_id=deposit.id,
        )

    deposit.reviewed_by = current_admin.id
    deposit.reviewed_at = datetime.now(timezone.utc)

    log_admin_action(
        db, current_admin.id,
        action="deposit.approved" if payload.approve else "deposit.rejected",
        target_type="crypto_deposit", target_id=deposit.id, target_user_id=deposit.user_id,
        summary=f"{'Approved' if payload.approve else 'Rejected'} {deposit.amount} {deposit.currency} deposit"
        + (f" ({payload.rejection_reason})" if not payload.approve else ""),
    )

    db.commit()
    db.refresh(deposit)
    return CryptoDepositOut.model_validate(deposit)


# --- NGN deposits (Paystack) ---

@router.post("/ngn/initialize", response_model=NGNDepositInitResponse)
def initialize_ngn_deposit(
    payload: NGNDepositInitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reference = f"much-exchange-{uuid.uuid4().hex[:20]}"

    paystack_response = initialize_transaction(current_user.email, float(payload.amount), reference)
    data = paystack_response.get("data", {})

    deposit = NGNDepositRequest(
        user_id=current_user.id,
        amount=payload.amount,
        paystack_reference=reference,
    )
    db.add(deposit)
    db.commit()

    return NGNDepositInitResponse(
        reference=reference,
        authorization_url=data.get("authorization_url", ""),
    )


def _finalize_ngn_deposit_if_successful(db: Session, reference: str) -> NGNDepositRequest | None:
    """Shared logic between the webhook and the manual verify fallback."""
    deposit = db.query(NGNDepositRequest).filter(NGNDepositRequest.paystack_reference == reference).with_for_update().populate_existing().first()
    if deposit is None or deposit.status != NGNDepositStatus.INITIALIZED:
        return deposit  # already processed, or unknown reference — caller decides how to respond

    verify_response = verify_transaction(reference)
    data = verify_response.get("data", {})

    from datetime import datetime, timezone

    if data.get("status") == "success":
        expected_kobo = int(round(float(deposit.amount) * 100))
        if data.get("amount") == expected_kobo:
            is_first = count_completed_ngn_deposits(db, deposit.user_id) == 0
            wallet = get_wallet(db, deposit.user_id, "NGN")
            credit_wallet(
                db, wallet, deposit.amount, tx_type=TransactionType.DEPOSIT,
                reference=reference, description="NGN deposit via Paystack",
            )
            depositor = db.query(User).filter(User.id == deposit.user_id).first()
            redeem_deposit_promotions(db, depositor, deposit.amount, is_first_deposit=is_first)
            deposit.status = NGNDepositStatus.SUCCESS
            deposit.verified_at = datetime.now(timezone.utc)
        else:
            # Amount mismatch — don't credit, flag as failed rather than trusting the claimed amount.
            deposit.status = NGNDepositStatus.FAILED
    else:
        deposit.status = NGNDepositStatus.FAILED

    db.commit()
    db.refresh(deposit)
    return deposit


@router.get("/ngn/verify/{reference}", response_model=NGNDepositOut)
def verify_ngn_deposit(
    reference: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manual fallback in case the webhook is delayed or missed — safe to call repeatedly."""
    # Check ownership first: a user must not be able to trigger processing
    # (and Paystack API calls) for someone else's payment reference.
    existing = db.query(NGNDepositRequest).filter(NGNDepositRequest.paystack_reference == reference).first()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deposit reference not found")
    if existing.user_id != current_user.id:
        # Same response as an unknown reference — don't confirm it exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deposit reference not found")
    deposit = _finalize_ngn_deposit_if_successful(db, reference)
    return NGNDepositOut.model_validate(deposit)


def _valid_paystack_signature(raw_body: bytes, signature: str | None) -> bool:
    """Paystack signs the raw request body with HMAC-SHA512 using the secret key."""
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/ngn/webhook")
@limiter.limit("30/minute")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Paystack calls this automatically when a payment completes.

    Only requests signed by Paystack (x-paystack-signature = HMAC-SHA512 of
    the raw body with our secret key) are processed; everything else is
    rejected before any work happens. Even a correctly signed event is
    never trusted for the amount: _finalize_ngn_deposit_if_successful
    re-verifies with Paystack's API and checks the amount itself.
    """
    raw = await request.body()
    if not _valid_paystack_signature(raw, request.headers.get("x-paystack-signature")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    # Acknowledge (200) events we don't act on so Paystack doesn't keep retrying them.
    if not isinstance(payload, dict) or payload.get("event") != "charge.success":
        return {"status": "ignored"}

    reference = (payload.get("data") or {}).get("reference")
    if not reference or not isinstance(reference, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing reference in webhook payload")

    # _finalize makes blocking HTTP + DB calls; run it off the event loop so a
    # slow Paystack response can't stall every other request.
    await run_in_threadpool(_finalize_ngn_deposit_if_successful, db, reference)
    return {"status": "ok"}


# --- NGN bank transfer deposits ---

@router.post("/ngn/bank-transfer/initiate", response_model=NGNBankTransferInitiateResponse, status_code=status.HTTP_201_CREATED)
def initiate_ngn_bank_transfer(
    payload: NGNBankTransferInitiate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.query(CompanyBankAccount).first()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bank transfer deposits aren't available yet — an admin needs to set up the company bank account first",
        )

    # Regenerate on the rare chance of a collision (astronomically
    # unlikely with 4 random bytes, but cheap to guard against anyway).
    reference_number = generate_reference_number()
    while db.query(NGNBankTransferDeposit).filter(NGNBankTransferDeposit.reference_number == reference_number).first():
        reference_number = generate_reference_number()

    deposit = NGNBankTransferDeposit(
        user_id=current_user.id,
        amount=payload.amount,
        reference_number=reference_number,
    )
    db.add(deposit)
    db.commit()
    db.refresh(deposit)

    return NGNBankTransferInitiateResponse(
        deposit=NGNBankTransferOut.model_validate(deposit),
        bank_name=account.bank_name,
        account_number=account.account_number,
        account_name=account.account_name,
    )


@router.get("/ngn/bank-transfer/me", response_model=list[NGNBankTransferOut])
def my_ngn_bank_transfers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deposits = (
        db.query(NGNBankTransferDeposit)
        .filter(NGNBankTransferDeposit.user_id == current_user.id)
        .order_by(NGNBankTransferDeposit.created_at.desc())
        .all()
    )
    return [NGNBankTransferOut.model_validate(d) for d in deposits]


@router.get("/ngn/bank-transfer/admin/pending", response_model=list[NGNBankTransferOut])
def list_pending_ngn_bank_transfers(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    deposits = (
        db.query(NGNBankTransferDeposit)
        .filter(NGNBankTransferDeposit.status == NGNBankTransferStatus.PENDING)
        .order_by(NGNBankTransferDeposit.created_at.asc())
        .all()
    )
    return [NGNBankTransferOut.model_validate(d) for d in deposits]


@router.post("/ngn/bank-transfer/admin/{deposit_id}/review", response_model=NGNBankTransferOut)
def review_ngn_bank_transfer(
    deposit_id: uuid.UUID,
    payload: NGNBankTransferReviewRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    deposit = db.query(NGNBankTransferDeposit).filter(NGNBankTransferDeposit.id == deposit_id).with_for_update().populate_existing().first()
    if deposit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deposit request not found")
    if deposit.status != NGNBankTransferStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This deposit has already been reviewed")

    if payload.approve:
        is_first = count_completed_ngn_deposits(db, deposit.user_id) == 0
        wallet = get_wallet(db, deposit.user_id, "NGN")
        credit_wallet(
            db, wallet, deposit.amount, tx_type=TransactionType.DEPOSIT,
            reference=deposit.reference_number,
            description=f"NGN bank transfer deposit approved (ref: {deposit.reference_number})",
            created_by_admin_id=current_admin.id,
        )
        depositor = db.query(User).filter(User.id == deposit.user_id).first()
        redeem_deposit_promotions(db, depositor, deposit.amount, is_first_deposit=is_first)
        deposit.status = NGNBankTransferStatus.APPROVED
        notify(
            db, deposit.user_id, NotificationType.DEPOSIT_APPROVED,
            "Deposit approved", f"Your NGN bank transfer deposit of {deposit.amount} (ref: {deposit.reference_number}) has been approved and credited.",
            related_id=deposit.id,
        )
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rejection_reason is required when rejecting")
        deposit.status = NGNBankTransferStatus.REJECTED
        deposit.rejection_reason = payload.rejection_reason
        notify(
            db, deposit.user_id, NotificationType.DEPOSIT_REJECTED,
            "Deposit rejected", f"Your NGN bank transfer deposit (ref: {deposit.reference_number}) was rejected: {payload.rejection_reason}",
            related_id=deposit.id,
        )

    deposit.reviewed_by = current_admin.id
    deposit.reviewed_at = datetime.now(timezone.utc)

    log_admin_action(
        db, current_admin.id,
        action="deposit.approved" if payload.approve else "deposit.rejected",
        target_type="ngn_bank_transfer_deposit", target_id=deposit.id, target_user_id=deposit.user_id,
        summary=f"{'Approved' if payload.approve else 'Rejected'} \u20a6{deposit.amount} NGN bank transfer deposit (ref: {deposit.reference_number})"
        + (f" ({payload.rejection_reason})" if not payload.approve else ""),
    )

    db.commit()
    db.refresh(deposit)
    return NGNBankTransferOut.model_validate(deposit)
