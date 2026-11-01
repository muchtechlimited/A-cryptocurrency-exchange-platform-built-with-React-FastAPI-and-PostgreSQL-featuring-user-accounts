import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.promotion import PromotionRewardType, PromotionTrigger

TriggerLiteral = Literal["signup", "kyc_approved", "first_deposit", "deposit_amount", "first_trade"]
RewardTypeLiteral = Literal["fixed", "percentage"]


class PromotionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    code: Optional[str] = Field(default=None, max_length=32)
    trigger: TriggerLiteral
    reward_type: RewardTypeLiteral
    reward_value: Decimal = Field(gt=0)
    reward_cap_ngn: Optional[Decimal] = Field(default=None, gt=0)
    min_amount_ngn: Optional[Decimal] = Field(default=None, gt=0)
    hold_days: int = Field(default=0, ge=0, le=90)
    stackable: bool = True
    requires_kyc: bool = True
    max_redemptions_total: Optional[int] = Field(default=None, gt=0)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.code is not None and self.trigger != PromotionTrigger.SIGNUP:
            raise ValueError("code can only be set on a signup-trigger promotion; every other trigger auto-applies")
        if self.trigger in PromotionTrigger.AMOUNTLESS:
            if self.reward_type == PromotionRewardType.PERCENTAGE:
                raise ValueError(f"'{self.trigger}' has no natural amount to take a percentage of — use a fixed reward")
            if self.min_amount_ngn is not None:
                raise ValueError(f"min_amount_ngn doesn't apply to '{self.trigger}'")
        if self.reward_type == PromotionRewardType.FIXED and self.reward_cap_ngn is not None:
            raise ValueError("reward_cap_ngn only applies to percentage rewards")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.code:
            self.code = self.code.strip().upper()
        return self


class PromotionUpdate(BaseModel):
    """Only the fields an admin should change after a campaign is live — the reward
    mechanics (trigger, reward_type/value, code) are fixed at creation so that past
    redemptions stay meaningful. Make a new promotion instead for a different offer."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    is_active: Optional[bool] = None
    max_redemptions_total: Optional[int] = Field(default=None, gt=0)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class PromotionOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    has_code: bool                      # never expose the actual code to anyone but the creating admin's own list
    trigger: str
    reward_type: str
    reward_value: float
    reward_cap_ngn: Optional[float]
    min_amount_ngn: Optional[float]
    hold_days: int
    stackable: bool
    requires_kyc: bool
    max_redemptions_total: Optional[int]
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    is_active: bool
    created_at: datetime


class PromotionAdminOut(PromotionOut):
    code: Optional[str]                 # admins may see the real code
    redemption_count: int
    paid_count: int
    paid_total_ngn: float


class PromotionRedemptionOut(BaseModel):
    id: uuid.UUID
    promotion_id: uuid.UUID
    promotion_name: str
    status: Literal["holding", "paid", "cancelled"]
    reward_amount_ngn: float
    qualifying_amount_ngn: Optional[float]
    release_at: Optional[datetime]
    paid_at: Optional[datetime]
    cancel_reason: Optional[str]
    created_at: datetime


class MyPromotionsOut(BaseModel):
    redemptions: list[PromotionRedemptionOut]
    pending_reward_ngn: float
    paid_total_ngn: float


class AdminRedemptionOut(PromotionRedemptionOut):
    user_id: uuid.UUID
    user_email: str
    user_name: str


class SettleResult(BaseModel):
    paid: int
