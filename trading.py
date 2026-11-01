"""
Schemas for Phase 5 — trading (buy/sell orders).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_serializer


def _fmt(value: Decimal) -> str:
    # See the identical comment in app/schemas/deposit_withdrawal.py —
    # quantizing first keeps formatting consistent regardless of whether
    # the Decimal came from raw user input or a DB column.
    return format(value.quantize(Decimal("0.00000001")), "f")


def _fmt_percentage(value: Decimal) -> str:
    """
    Fee percentages are stored with 3 decimal places (Numeric(6,3)), not
    8 like crypto/NGN amounts — quantizing these to 8 decimals would
    turn "0.500" into "0.50000000", which is technically still correct
    but doesn't match how a percentage is naturally expressed. Kept as
    a separate formatter rather than overloading `_fmt` for both.
    """
    return format(value.quantize(Decimal("0.001")), "f")


class OrderRequest(BaseModel):
    """Used for both the preview and the actual placement — same inputs either way."""
    currency: Literal["BTC", "USDT"]
    crypto_amount: Decimal = Field(gt=0)


class OrderPreview(BaseModel):
    """
    Shown to the user before they confirm — matches the order summary
    screen described in the roadmap (amount, rate, total, fee, net).
    Nothing is created or frozen when this is generated.
    """
    side: str
    currency: str
    crypto_amount: Decimal
    rate_applied: Decimal
    ngn_amount: Decimal
    fee_percentage: Decimal
    fee_amount_ngn: Decimal
    net_ngn_amount: Decimal
    # For a buy: net_ngn_amount is what the user pays (ngn_amount + fee)
    # For a sell: net_ngn_amount is what the user receives (ngn_amount - fee)

    @field_serializer("crypto_amount", "rate_applied", "ngn_amount", "fee_amount_ngn", "net_ngn_amount")
    def serialize_decimals(self, value: Decimal) -> str:
        return _fmt(value)

    @field_serializer("fee_percentage")
    def serialize_percentage(self, value: Decimal) -> str:
        return _fmt_percentage(value)


class OrderOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    side: str
    currency: str
    crypto_amount: Decimal
    rate_applied: Decimal
    fee_percentage: Decimal
    fee_amount_ngn: Decimal
    ngn_amount: Decimal
    net_ngn_amount: Decimal
    status: str
    rejection_reason: Optional[str]
    rejection_action: Optional[str]
    created_at: datetime
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @field_serializer("crypto_amount", "rate_applied", "fee_amount_ngn", "ngn_amount", "net_ngn_amount")
    def serialize_decimals(self, value: Decimal) -> str:
        return _fmt(value)

    @field_serializer("fee_percentage")
    def serialize_percentage(self, value: Decimal) -> str:
        return _fmt_percentage(value)


class OrderReviewRequest(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    rejection_action: Optional[Literal["unfreeze", "keep_frozen"]] = Field(
        default=None,
        description="Required when approve=false — admin must explicitly choose what happens to the frozen amount",
    )


class ExchangeRateOut(BaseModel):
    currency: str
    buy_rate_ngn: Decimal
    sell_rate_ngn: Decimal
    buy_fee_percentage: Decimal
    sell_fee_percentage: Decimal
    withdrawal_network_fee: Decimal
    withdrawal_service_fee: Decimal
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("buy_rate_ngn", "sell_rate_ngn", "withdrawal_network_fee", "withdrawal_service_fee")
    def serialize_decimals(self, value: Decimal) -> str:
        return _fmt(value)

    @field_serializer("buy_fee_percentage", "sell_fee_percentage")
    def serialize_percentage(self, value: Decimal) -> str:
        return _fmt_percentage(value)


class ExchangeRateUpdate(BaseModel):
    buy_rate_ngn: Decimal = Field(gt=0)
    sell_rate_ngn: Decimal = Field(gt=0)
    buy_fee_percentage: Decimal = Field(ge=0, default=Decimal("0"))
    sell_fee_percentage: Decimal = Field(ge=0, default=Decimal("0"))
    withdrawal_network_fee: Decimal = Field(ge=0, default=Decimal("0"))
    withdrawal_service_fee: Decimal = Field(ge=0, default=Decimal("0"))
