"""
Wallet routes.

User-facing:
  GET  /wallets/me                    - all my wallet balances (NGN, BTC, USDT)
  GET  /wallets/me/transactions       - my transaction history

Admin-facing (matches the admin Transactions page):
  GET  /wallets/admin/transactions    - every transaction, across all users
  POST /wallets/admin/users/{user_id}/credit  - manually credit a user's wallet
  POST /wallets/admin/users/{user_id}/debit   - manually debit a user's wallet
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.models.notification import NotificationType
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.models.wallet import Currency, Wallet
from app.schemas.wallet import AdminWalletAdjustment, TransactionOut, WalletOut
from app.utils.promotion_service import settle_due as settle_due_promotions
from app.utils.referral_service import settle_due
from app.utils.audit import log_admin_action
from app.utils.notification_service import notify
from app.utils.wallet_service import credit_wallet, debit_wallet, get_wallet

router = APIRouter()


def _ensure_all_wallets_exist(db: Session, user_id: uuid.UUID) -> list[Wallet]:
    """
    Self-healing helper: creates any wallet the user is missing. Needed
    because wallets are created at registration going forward, but any
    account created before this feature shipped won't have them yet.
    """
    existing = db.query(Wallet).filter(Wallet.user_id == user_id).all()
    existing_currencies = {w.currency for w in existing}

    missing = [c for c in Currency.ALL if c not in existing_currencies]
    for currency in missing:
        wallet = Wallet(user_id=user_id, currency=currency)
        db.add(wallet)
        existing.append(wallet)

    if missing:
        db.commit()
        for w in existing:
            db.refresh(w)

    return existing


# --- User-facing ---

@router.get("/me", response_model=list[WalletOut])
def get_my_wallets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settle_due(db, referrer_id=current_user.id)   # pay any referral reward whose 7-day hold has elapsed
    settle_due_promotions(db, user_id=current_user.id)   # pay any promotion reward whose hold has elapsed
    wallets = _ensure_all_wallets_exist(db, current_user.id)
    return [WalletOut.model_validate(w) for w in wallets]


@router.get("/me/transactions", response_model=list[TransactionOut])
def get_my_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    transactions = (
        db.query(Transaction)
        .filter(Transaction.user_id == current_user.id)
        .order_by(Transaction.created_at.desc())
        .all()
    )
    return [TransactionOut.model_validate(t) for t in transactions]


# --- Admin-facing ---

@router.get("/admin/transactions", response_model=list[TransactionOut])
def list_all_transactions(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    transactions = db.query(Transaction).order_by(Transaction.created_at.desc()).all()
    return [TransactionOut.model_validate(t) for t in transactions]


@router.post("/admin/users/{user_id}/credit", response_model=TransactionOut)
def admin_credit_wallet(
    user_id: uuid.UUID,
    payload: AdminWalletAdjustment,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _ensure_all_wallets_exist(db, user_id)
    wallet = get_wallet(db, user_id, payload.currency)

    txn = credit_wallet(
        db,
        wallet,
        payload.amount,
        tx_type=TransactionType.ADMIN_CREDIT,
        description=payload.description,
        created_by_admin_id=current_admin.id,
    )
    notify(
        db, user_id, NotificationType.ADMIN_ADJUSTMENT,
        "Wallet credited", f"An admin credited your {payload.currency} wallet with {payload.amount}: {payload.description}",
    )
    log_admin_action(
        db, current_admin.id,
        action="wallet.credited", target_type="wallet", target_id=wallet.id, target_user_id=user_id,
        summary=f"Credited {payload.amount} {payload.currency} — {payload.description}",
        details={"amount": float(payload.amount), "currency": payload.currency, "description": payload.description},
    )
    db.commit()
    db.refresh(txn)
    return TransactionOut.model_validate(txn)


@router.post("/admin/users/{user_id}/debit", response_model=TransactionOut)
def admin_debit_wallet(
    user_id: uuid.UUID,
    payload: AdminWalletAdjustment,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    _ensure_all_wallets_exist(db, user_id)
    wallet = get_wallet(db, user_id, payload.currency)

    txn = debit_wallet(
        db,
        wallet,
        payload.amount,
        tx_type=TransactionType.ADMIN_DEBIT,
        description=payload.description,
        created_by_admin_id=current_admin.id,
    )
    notify(
        db, user_id, NotificationType.ADMIN_ADJUSTMENT,
        "Wallet debited", f"An admin debited your {payload.currency} wallet by {payload.amount}: {payload.description}",
    )
    log_admin_action(
        db, current_admin.id,
        action="wallet.debited", target_type="wallet", target_id=wallet.id, target_user_id=user_id,
        summary=f"Debited {payload.amount} {payload.currency} — {payload.description}",
        details={"amount": float(payload.amount), "currency": payload.currency, "description": payload.description},
    )
    db.commit()
    db.refresh(txn)
    return TransactionOut.model_validate(txn)
