"""
Company wallet address routes.

  GET /company-wallets                  - view current deposit addresses (any logged-in user)
  PUT /company-wallets/admin/{currency} - admin sets/updates the address for a currency

Company bank account routes (NGN bank transfer deposits):
  GET /company-wallets/bank-account       - view current company bank account (any logged-in user)
  PUT /company-wallets/admin/bank-account - admin sets/updates the company bank account
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user_or_admin
from app.models.company_bank_account import CompanyBankAccount
from app.models.company_wallet import CompanyWalletAddress
from app.models.user import User
from app.schemas.deposit_withdrawal import (
    CompanyBankAccountOut,
    CompanyBankAccountUpdate,
    CompanyWalletAddressOut,
    CompanyWalletAddressUpdate,
)
from app.utils.audit import log_admin_action

router = APIRouter()


@router.get("", response_model=list[CompanyWalletAddressOut])
def list_company_wallets(
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    addresses = db.query(CompanyWalletAddress).all()
    return [CompanyWalletAddressOut.model_validate(a) for a in addresses]


@router.get("/bank-account", response_model=CompanyBankAccountOut)
def get_company_bank_account(
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    account = db.query(CompanyBankAccount).first()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No company bank account has been set up yet — an admin needs to configure it first",
        )
    return CompanyBankAccountOut.model_validate(account)


@router.put("/admin/bank-account", response_model=CompanyBankAccountOut)
def set_company_bank_account(
    payload: CompanyBankAccountUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    account = db.query(CompanyBankAccount).first()
    if account is None:
        account = CompanyBankAccount()
        db.add(account)

    account.bank_name = payload.bank_name
    account.account_number = payload.account_number
    account.account_name = payload.account_name
    account.updated_by_admin_id = current_admin.id

    log_admin_action(
        db, current_admin.id,
        action="company_bank_account.updated", target_type="company_bank_account",
        summary=f"Set company bank account to {payload.bank_name} \u2022\u2022\u2022{payload.account_number[-4:]} ({payload.account_name})",
    )

    db.commit()
    db.refresh(account)
    return CompanyBankAccountOut.model_validate(account)


# NOTE: this generic /admin/{currency} route MUST be registered AFTER
# /admin/bank-account above — FastAPI matches routes in registration
# order, and this dynamic path would otherwise greedily swallow
# "/admin/bank-account" too, treating "bank-account" as a currency value.
@router.put("/admin/{currency}", response_model=CompanyWalletAddressOut)
def set_company_wallet(
    currency: str,
    payload: CompanyWalletAddressUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    entry = db.query(CompanyWalletAddress).filter(CompanyWalletAddress.currency == currency).first()
    if entry is None:
        entry = CompanyWalletAddress(currency=currency)
        db.add(entry)

    entry.address = payload.address
    entry.network_note = payload.network_note
    entry.updated_by_admin_id = current_admin.id

    log_admin_action(
        db, current_admin.id,
        action="company_wallet.updated", target_type="company_wallet_address", target_id=currency,
        summary=f"Set company {currency} deposit address to {payload.address}",
    )

    db.commit()
    db.refresh(entry)
    return CompanyWalletAddressOut.model_validate(entry)
