"""
Pydantic schemas for users and auth flows.
Keep these separate from the SQLAlchemy model (app/models/user.py) —
schemas define what the API accepts/returns, the model defines the table.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

# --- Requests ---

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=100)
    bank_name: str = Field(min_length=1, max_length=100)
    account_number: str = Field(pattern=r"^\d{10}$", description="10-digit NUBAN account number")
    account_name: str = Field(min_length=3, max_length=100)
    bvn: Optional[str] = Field(default=None, pattern=r"^\d{11}$", description="11-digit BVN, optional")
    referral_code: Optional[str] = Field(default=None, max_length=32, description="Optional referral code from an existing customer")
    promo_code: Optional[str] = Field(default=None, max_length=32, description="Optional promotion code (independent of referral_code)")

class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# --- Responses ---

class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_active: bool
    is_verified: bool
    is_admin: bool
    # Admin role (super_admin, operations_admin, ...); None for customers.
    role: Optional[str] = None
    created_at: datetime
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    # Returned so the frontend can render the user's photo in the topbar /
    # nav drawer straight from the login response, without a second
    # round-trip to /users/me/profile on every page load.
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MessageResponse(BaseModel):
    message: str
