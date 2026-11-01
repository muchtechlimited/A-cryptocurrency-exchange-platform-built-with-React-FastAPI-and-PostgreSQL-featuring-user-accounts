"""
NGN deposit model — tracks Paystack transactions.

Unlike crypto deposits (manually reviewed by an admin), NGN deposits
via Paystack are verified automatically: we initialize a transaction,
the user pays on Paystack's checkout page, and Paystack calls our
webhook (or we call their verify endpoint) to confirm payment before
crediting the wallet. No admin approval step for NGN deposits.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class NGNDepositStatus:
    INITIALIZED = "initialized"  # Paystack transaction created, awaiting payment
    SUCCESS = "success"          # verified paid, wallet credited
    FAILED = "failed"
    ALL = [INITIALIZED, SUCCESS, FAILED]


class NGNDepositRequest(Base):
    __tablename__ = "ngn_deposit_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    amount = Column(Numeric(24, 8), nullable=False)
    paystack_reference = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, default=NGNDepositStatus.INITIALIZED, nullable=False)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
