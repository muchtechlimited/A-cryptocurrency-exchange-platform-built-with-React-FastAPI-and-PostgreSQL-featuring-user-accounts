"""
Schemas for Phase 4 — wallets and transactions.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_serializer


def _format_decimal(value: Decimal) -> str:
    """Always render as fixed-point (e.g. '0.00000000'), never scientific notation ('0E-8').
    Quantizes first so formatting is consistent regardless of whether the
    Decimal came from raw user input or a DB column — see the identical
    comment in app/schemas/deposit_withdrawal.py."""
    return format(value.quantize(Decimal("0.00000001")), "f")


class WalletOut(BaseModel):
    id: uuid.UUID
    currency: str
    available_balance: Decimal
    frozen_balance: Decimal
    total_balance: Decimal
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("available_balance", "frozen_balance", "total_balance")
    def serialize_balances(self, value: Decimal) -> str:
        return _format_decimal(value)


class TransactionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    wallet_id: uuid.UUID
    type: str
    status: str
    currency: str
    amount: Decimal
    fee_amount: Decimal
    balance_after: Optional[Decimal]
    reference: Optional[str]
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("amount", "fee_amount")
    def serialize_amounts(self, value: Decimal) -> str:
        return _format_decimal(value)

    @field_serializer("balance_after")
    def serialize_balance_after(self, value: Optional[Decimal]) -> Optional[str]:
        return _format_decimal(value) if value is not None else None


class AdminWalletAdjustment(BaseModel):
    """Admin manually crediting or debiting a user's wallet (e.g. correcting an error)."""
    currency: str
    amount: Decimal
    description: str
