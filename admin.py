import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.wallet import WalletOut


class AdminUserListItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_verified: bool
    is_admin: bool
    kyc_status: str
    created_at: datetime
    # Admin role (None for customers).
    role: Optional[str] = None

    model_config = {"from_attributes": True}


class AdminUserDetail(AdminUserListItem):
    phone_number: Optional[str]
    country: Optional[str]
    wallets: list[WalletOut]
    total_orders: int
    total_deposits: int
    total_withdrawals: int


class AdminAccountItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    role: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminRoleUpdate(BaseModel):
    role: str


class UserStatusUpdate(BaseModel):
    is_active: bool


class DashboardStats(BaseModel):
    total_users: int
    verified_users: int
    pending_kyc: int
    pending_orders: int
    pending_crypto_deposits: int
    pending_crypto_withdrawals: int
    total_transactions: int


class DailyActivityPoint(BaseModel):
    date: str
    new_users: int
    transactions: int


class DashboardAnalytics(BaseModel):
    stats: DashboardStats
    daily_activity: list[DailyActivityPoint]


# ---- Analytics (roadmap Priority 6) ----

class AnalyticsDailyPoint(BaseModel):
    date: str
    trading_volume_ngn: float      # gross NGN value of orders approved that day
    revenue_ngn: float             # trading fees on orders approved that day
    ngn_deposits: float            # completed NGN deposits
    ngn_withdrawals: float         # completed NGN withdrawals
    new_users: int


class AssetTradeStat(BaseModel):
    currency: str
    order_count: int
    volume_ngn: float
    revenue_ngn: float


class KycFunnel(BaseModel):
    new_users: int                 # registered in the period
    submitted_kyc: int             # of those, users with >= 1 KYC submission
    approved_kyc: int              # of those, users whose KYC is approved now
    conversion_pct: float          # approved / new_users * 100 (0 if no new users)


class AnalyticsTotals(BaseModel):
    trading_volume_ngn: float
    revenue_ngn: float
    ngn_deposits: float
    ngn_withdrawals: float
    approved_orders: int
    new_users: int


class AnalyticsOut(BaseModel):
    days: int
    totals: AnalyticsTotals
    daily: list[AnalyticsDailyPoint]
    assets: list[AssetTradeStat]   # sorted by volume, most traded first
    most_traded_asset: Optional[str]
    kyc: KycFunnel


class LoginAttemptItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str] = None
    role: Optional[str] = None
    success: bool
    reason: Optional[str] = None
    stage: str
    ip_address: Optional[str] = None
    device_label: Optional[str] = None
    created_at: datetime


class FailingIp(BaseModel):
    ip_address: str
    failures: int
    accounts: int
    last_seen: datetime


class LockedAdmin(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    locked_until: datetime


class SecurityEventsOut(BaseModel):
    failed_24h: int
    failed_7d: int
    successful_24h: int
    unknown_email_failures_24h: int
    locked_admins: list[LockedAdmin]
    top_failing_ips: list[FailingIp]
    attempts: list[LoginAttemptItem]


class ServiceHealth(BaseModel):
    id: str
    label: str
    status: str  # connected | delayed | offline | unknown
    ms: Optional[int] = None
    last_ok: Optional[datetime] = None
    error: Optional[str] = None
    note: Optional[str] = None


class SystemHealthOut(BaseModel):
    checked_at: datetime
    services: list[ServiceHealth]
