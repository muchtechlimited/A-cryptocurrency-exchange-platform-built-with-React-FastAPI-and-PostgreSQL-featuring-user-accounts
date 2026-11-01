"""
Schemas for Phase 6 — deposits and withdrawals.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_serializer


def _fmt(value: Decimal) -> str:
    # Always quantize to 8 decimal places before formatting — a Decimal
    # built from raw user input (e.g. "0.01" from a JSON request body)
    # doesn't carry the same precision as one pulled from a NUMERIC(24,8)
    # database column, so without this, the same field could render as
    # "0.01" in one response and "0.01000000" in another depending on
    # where the value came from. Quantizing first makes it consistent
    # everywhere, regardless of source.
    return format(value.quantize(Decimal("0.00000001")), "f")


# --- Company wallet addresses ---

class CompanyWalletAddressOut(BaseModel):
    currency: str
    address: str
    network_note: Optional[str]
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompanyWalletAddressUpdate(BaseModel):
    address: str = Field(min_length=1)
    network_note: Optional[str] = None


# --- Crypto deposits ---

class CryptoDepositSubmit(BaseModel):
    currency: Literal["BTC", "USDT"]
    amount: Decimal = Field(gt=0)
    tx_hash: str = Field(min_length=1, max_length=200)


class CryptoDepositOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    currency: str
    amount: Decimal
    tx_hash: str
    status: str
    rejection_reason: Optional[str]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return _fmt(value)


class DepositReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(default=None, max_length=500)


# --- Crypto withdrawals ---

class CryptoWithdrawalSubmit(BaseModel):
    currency: Literal["BTC", "USDT"]
    amount: Decimal = Field(gt=0)
    destination_address: str = Field(min_length=1, max_length=200)


class CryptoWithdrawalPreview(BaseModel):
    """
    Shown to the user before they confirm a withdrawal — matches the
    same order-summary pattern as the Phase 5 trading preview. Nothing
    is created or frozen when this is generated.
    """
    currency: str
    wallet_balance: Decimal
    amount: Decimal
    destination_address: str
    network_fee_amount: Decimal
    service_fee_amount: Decimal
    total_fees: Decimal
    net_amount: Decimal  # what the user will actually receive at destination_address

    @field_serializer(
        "wallet_balance", "amount", "network_fee_amount",
        "service_fee_amount", "total_fees", "net_amount",
    )
    def serialize_decimals(self, value: Decimal) -> str:
        return _fmt(value)


class CryptoWithdrawalOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    currency: str
    amount: Decimal
    network_fee_amount: Decimal
    service_fee_amount: Decimal
    net_amount: Decimal
    destination_address: str
    status: str
    rejection_reason: Optional[str]
    rejection_action: Optional[str]
    admin_tx_hash: Optional[str]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("amount", "network_fee_amount", "service_fee_amount", "net_amount")
    def serialize_amount(self, value: Decimal) -> str:
        return _fmt(value)


class WithdrawalReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    rejection_action: Optional[Literal["unfreeze", "keep_frozen"]] = Field(
        default=None,
        description="Required when approve=false",
    )


class WithdrawalCompleteRequest(BaseModel):
    admin_tx_hash: str = Field(min_length=1, max_length=200)


# --- NGN deposits (Paystack) ---

class NGNDepositInitRequest(BaseModel):
    amount: Decimal = Field(gt=0)


class NGNDepositInitResponse(BaseModel):
    reference: str
    authorization_url: str


class NGNDepositOut(BaseModel):
    id: uuid.UUID
    amount: Decimal
    paystack_reference: str
    status: str
    created_at: datetime
    verified_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return _fmt(value)


# --- Company bank account ---

class CompanyBankAccountOut(BaseModel):
    bank_name: str
    account_number: str
    account_name: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class CompanyBankAccountUpdate(BaseModel):
    bank_name: str = Field(min_length=1, max_length=100)
    account_number: str = Field(pattern=r"^\d{10}$", description="10-digit NUBAN account number")
    account_name: str = Field(min_length=1, max_length=100)


# --- NGN bank transfer deposits ---

# Shown alongside every reference number — Nigerian banks frequently
# flag or freeze transfers whose narration mentions crypto-related
# terms, so users must NEVER put any of these words in the transfer
# description, only the reference number itself.
NGN_BANK_TRANSFER_WARNING = (
    "IMPORTANT: When making this transfer, use ONLY the reference number below as your "
    "transfer description/narration. Do NOT include words like 'bitcoin', 'BTC', 'crypto', "
    "'USDT', 'exchange', or similar in the transfer description — many Nigerian banks flag or "
    "block transfers that mention crypto-related terms, which can delay your deposit or freeze "
    "your bank account."
)


class NGNBankTransferInitiate(BaseModel):
    amount: Decimal = Field(gt=0)


class NGNBankTransferOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    reference_number: str
    status: str
    rejection_reason: Optional[str]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return _fmt(value)


class NGNBankTransferInitiateResponse(BaseModel):
    deposit: NGNBankTransferOut
    bank_name: str
    account_number: str
    account_name: str
    warning: str = NGN_BANK_TRANSFER_WARNING


class NGNBankTransferReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(default=None, max_length=500)


# --- NGN withdrawals ---
# See app/models/ngn_withdrawal.py for why this is single-step (approve
# finalizes the debit directly, no separate "completed" state) unlike
# crypto withdrawals.

class NGNWithdrawalSubmit(BaseModel):
    amount: Decimal = Field(gt=0)


class NGNWithdrawalPreview(BaseModel):
    wallet_balance: Decimal
    amount: Decimal
    service_fee_amount: Decimal
    net_amount: Decimal
    destination_bank_name: str
    destination_account_number: str
    destination_account_name: str

    @field_serializer("wallet_balance", "amount", "service_fee_amount", "net_amount")
    def serialize_decimals(self, value: Decimal) -> str:
        return _fmt(value)


class NGNWithdrawalOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    service_fee_amount: Decimal
    net_amount: Decimal
    destination_bank_name: str
    destination_account_number: str
    destination_account_name: str
    status: str
    rejection_reason: Optional[str]
    rejection_action: Optional[str]
    admin_reference: Optional[str]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("amount", "service_fee_amount", "net_amount")
    def serialize_amount(self, value: Decimal) -> str:
        return _fmt(value)


class NGNWithdrawalReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    rejection_action: Optional[Literal["unfreeze", "keep_frozen"]] = Field(
        default=None,
        description="Required when approve=false",
    )
    admin_reference: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional, only used when approve=true — e.g. the admin's own bank transfer reference, for the audit trail",
    )
