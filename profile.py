"""
Schemas for Phase 3 — user profile and settings.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Profile ---

class ProfileUpdate(BaseModel):
    """All fields optional — this is a partial update (PATCH-style via PUT)."""
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    phone_number: Optional[str] = Field(default=None, max_length=20)
    country: Optional[str] = Field(default=None, max_length=100)
    avatar_url: Optional[str] = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone_number: Optional[str]
    country: Optional[str]
    avatar_url: Optional[str]
    kyc_status: str
    is_verified: bool
    created_at: datetime
    bank_name: Optional[str]
    account_number: Optional[str]
    account_name: Optional[str]
    bvn: Optional[str]

    model_config = {"from_attributes": True}


# --- Settings ---

class SettingsUpdate(BaseModel):
    email_notifications_enabled: Optional[bool] = None
    sms_notifications_enabled: Optional[bool] = None
    # Deliberately no two_factor_enabled here: turning 2FA on or off
    # must go through app/routers/two_factor.py's enable/disable
    # endpoints, which require proving a valid code first. A blind
    # boolean flip through this generic settings PATCH would let
    # anyone with just a session token silently disable someone's 2FA.


class SettingsOut(BaseModel):
    email_notifications_enabled: bool
    sms_notifications_enabled: bool
    two_factor_enabled: bool

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
