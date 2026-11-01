"""
Company bank account model.

Single row holding the company's own NGN bank account details, shown to
users depositing via direct bank transfer (as opposed to Paystack card
payments). Same "one shared destination, admin-managed" pattern as
CompanyWalletAddress (crypto), just for a bank account instead of a
crypto address.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class CompanyBankAccount(Base):
    __tablename__ = "company_bank_account"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_name = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    account_name = Column(String, nullable=False)

    updated_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
