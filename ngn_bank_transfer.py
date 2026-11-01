"""
NGN bank transfer deposit model.

Unlike Paystack deposits (auto-verified) or crypto deposits (user
submits a TxID after sending), this flow generates a unique reference
number UPFRONT, before the user sends anything. The user includes that
reference number as the transfer narration/description on their own
bank app, then an admin checks the company bank account's statement,
matches the incoming transfer by reference number, and approves —
crediting the user's NGN wallet with the amount they declared.

This exists specifically because Nigerian banks frequently flag or
block transfers whose narration mentions crypto-related terms (e.g.
"bitcoin", "BTC", "crypto") — hence the reference number, and the
explicit warning shown to users not to reference crypto at all in the
transfer description (see app/routers/deposits.py).
"""

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def generate_reference_number() -> str:
    """e.g. 'ME-7F3A9C2B' — short, human-typeable into a bank transfer narration field."""
    return "ME-" + secrets.token_hex(4).upper()


class NGNBankTransferStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALL = [PENDING, APPROVED, REJECTED]


class NGNBankTransferDeposit(Base):
    __tablename__ = "ngn_bank_transfer_deposits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    amount = Column(Numeric(24, 8), nullable=False)  # amount the user declares they will send
    reference_number = Column(String, unique=True, nullable=False, index=True)

    status = Column(String, default=NGNBankTransferStatus.PENDING, nullable=False)
    rejection_reason = Column(Text, nullable=True)

    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
