"""
Schemas for gift card sell submissions (seller-facing + admin review).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_serializer


def _fmt(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.00000001")), "f")


def _fmt2(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _fmt4(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


class GiftCardSubmitRequest(BaseModel):
    brand: str
    card_type: str  # "e-code" | "physical"
    card_country: str
    card_currency: str
    card_denomination: Decimal = Field(gt=0)
    redemption_code: str = Field(min_length=1, max_length=200)
    payment_method: str  # "bank_transfer" | "wallet_balance"


class GiftCardOut(BaseModel):
    """Seller's own view — never includes the redemption code."""
    id: uuid.UUID
    user_id: uuid.UUID
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal
    payment_method: str
    much_exchange_rate: Decimal
    expected_payout: Decimal
    actual_payout: Optional[Decimal]
    verification_result: str
    status: str
    rejection_reason: Optional[str]
    has_card_image: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("card_denomination", "much_exchange_rate")
    def _ser4(self, value: Decimal) -> str:
        return _fmt4(value)

    @field_serializer("expected_payout", "actual_payout")
    def _ser8(self, value: Optional[Decimal]) -> Optional[str]:
        return _fmt(value) if value is not None else None


class GiftCardAdminDetail(GiftCardOut):
    """Full admin view — includes the extended vendor/verification/audit fields."""
    vendor: Optional[str]
    vendor_transaction_id: Optional[str]
    current_vendor_rate: Optional[Decimal]
    api_webhook_status: str
    verification_notes: Optional[str]
    reviewed_by: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    redemption_code_masked: str = ""
    user_email: Optional[str] = None
    user_full_name: Optional[str] = None
    user_phone: Optional[str] = None

    @field_serializer("current_vendor_rate")
    def _ser_vendor_rate(self, value: Optional[Decimal]) -> Optional[str]:
        return _fmt4(value) if value is not None else None


class GiftCardVerifyRequest(BaseModel):
    """Admin records the result of checking the card with a redemption/verification vendor."""
    vendor: str = Field(min_length=1, max_length=100)
    vendor_transaction_id: Optional[str] = Field(default=None, max_length=200)
    current_vendor_rate: Optional[Decimal] = Field(default=None, gt=0)
    verification_result: str  # GiftCardVerificationResult
    verification_notes: Optional[str] = Field(default=None, max_length=1000)
    api_webhook_status: Optional[str] = None  # GiftCardApiWebhookStatus; defaults to "pending" if omitted


class GiftCardReviewRequest(BaseModel):
    approve: bool
    actual_payout: Optional[Decimal] = Field(
        default=None,
        gt=0,
        description="Defaults to expected_payout if omitted. Set explicitly when verification found a different (e.g. partial) value.",
    )
    rejection_reason: Optional[str] = Field(default=None, max_length=500)


class GiftCardAuditLogOut(BaseModel):
    id: uuid.UUID
    actor_id: Optional[uuid.UUID]
    actor_name: Optional[str] = None
    action: str
    details: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class GiftCardRateOut(BaseModel):
    brand: str
    buy_rate_ngn: Decimal
    sell_rate_ngn: Decimal
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("buy_rate_ngn", "sell_rate_ngn")
    def _ser(self, value: Decimal) -> str:
        return _fmt4(value)


class GiftCardRateUpdate(BaseModel):
    buy_rate_ngn: Decimal = Field(gt=0)
    sell_rate_ngn: Decimal = Field(gt=0)


class GiftCardStatsOut(BaseModel):
    pending_count: int
    approved_count: int
    rejected_count: int
    total_value_ngn: Decimal

    @field_serializer("total_value_ngn")
    def _ser(self, value: Decimal) -> str:
        return _fmt2(value)


# --- Buy side (Much Exchange sells gift cards to users) ---

class GiftCardCatalogEntry(BaseModel):
    """One buyable brand/denomination combo, aggregated from available stock."""
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal
    sell_rate_ngn: Decimal
    price_ngn: Decimal  # card_denomination * sell_rate_ngn
    quantity_available: int

    @field_serializer("card_denomination", "sell_rate_ngn")
    def _ser4(self, value: Decimal) -> str:
        return _fmt4(value)

    @field_serializer("price_ngn")
    def _ser8(self, value: Decimal) -> str:
        return _fmt(value)


class GiftCardStockAddRequest(BaseModel):
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal = Field(gt=0)
    codes: list[str] = Field(min_length=1, description="One or more redemption codes to add as available stock")


class GiftCardStockOut(BaseModel):
    id: uuid.UUID
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal
    status: str
    code_masked: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("card_denomination")
    def _ser(self, value: Decimal) -> str:
        return _fmt4(value)


class GiftCardPurchaseRequest(BaseModel):
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal = Field(gt=0)
    payment_method: str = "wallet_balance"


class GiftCardPurchaseOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    brand: str
    card_type: str
    card_country: str
    card_currency: str
    card_denomination: Decimal
    sell_rate_ngn: Decimal
    total_cost_ngn: Decimal
    payment_method: str
    status: str
    delivered_code: Optional[str] = None  # only populated for the buyer's own view, right after purchase
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("card_denomination", "sell_rate_ngn")
    def _ser4(self, value: Decimal) -> str:
        return _fmt4(value)

    @field_serializer("total_cost_ngn")
    def _ser8(self, value: Decimal) -> str:
        return _fmt(value)
