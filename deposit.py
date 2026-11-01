"""
Crypto deposit request model.

Flow: user sends crypto to the company's shared address externally,
then submits this request with the amount + transaction hash (TxID).
Admin manually verifies the transaction on the blockchain (outside this
app — no blockchain monitoring integration in scope) and approves,
which credits the user's wallet.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class DepositStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALL = [PENDING, APPROVED, REJECTED]


class CryptoDepositRequest(Base):
    __tablename__ = "crypto_deposit_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    currency = Column(String, nullable=False)  # "BTC" | "USDT"
    amount = Column(Numeric(24, 8), nullable=False)  # as claimed by the user, confirmed by admin on approval
    tx_hash = Column(String, nullable=False, index=True)  # blockchain TxID the user submits as proof

    status = Column(String, default=DepositStatus.PENDING, nullable=False)
    rejection_reason = Column(Text, nullable=True)

    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
