import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class ReferralItemOut(BaseModel):
    id: uuid.UUID
    referee_label: str                 # "David O." - never the full name or email
    status: Literal["pending", "holding", "under_review", "paid", "cancelled"]
    kyc_done: bool
    first_txn_done: bool
    reward_amount_ngn: Optional[float]
    release_at: Optional[datetime]
    paid_at: Optional[datetime]
    created_at: datetime


class ReferralSummaryOut(BaseModel):
    code: str
    reward_amount_ngn: float
    hold_days: int
    total_referred: int
    paid_count: int
    paid_total_ngn: float
    pending_reward_ngn: float          # "Pending Referral Reward": holding only
    under_review_count: int
    referrals: list[ReferralItemOut]


class AdminReferralOut(BaseModel):
    id: uuid.UUID
    referrer_id: uuid.UUID
    referrer_email: str
    referrer_name: str
    referee_id: uuid.UUID
    referee_email: str
    referee_name: str
    status: str
    kyc_done: bool
    first_txn_done: bool
    reward_amount_ngn: Optional[float]
    flag_reason: Optional[str]
    cancel_reason: Optional[str]
    release_at: Optional[datetime]
    paid_at: Optional[datetime]
    created_at: datetime


class AdminReferralReview(BaseModel):
    action: Literal["approve", "cancel"]
    reason: Optional[str] = None       # required for cancel


class SettleResult(BaseModel):
    paid: int
