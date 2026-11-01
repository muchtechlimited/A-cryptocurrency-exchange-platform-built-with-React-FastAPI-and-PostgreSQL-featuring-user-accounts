"""
Crypto withdrawal request model.

Flow: user submits destination address + amount. The amount is frozen
immediately on the user's wallet (same pattern as Phase 5 orders —
consistent freeze-then-admin-decides design). Admin reviews, manually
sends the crypto externally, records the blockchain TxID, and the
system marks it completed and finalizes the wallet debit.

Rejection mirrors Phase 5's order rejection: admin explicitly chooses
"unfreeze" or "keep_frozen" — never automatic.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class WithdrawalStatus:
    PENDING = "pending"        # awaiting admin review
    APPROVED = "approved"      # admin approved, awaiting the manual send + TxID
    COMPLETED = "completed"    # admin has sent the crypto and recorded the TxID
    REJECTED = "rejected"
    ALL = [PENDING, APPROVED, COMPLETED, REJECTED]


class CryptoWithdrawalRequest(Base):
    __tablename__ = "crypto_withdrawal_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    currency = Column(String, nullable=False)  # "BTC" | "USDT"
    amount = Column(Numeric(24, 8), nullable=False)
    destination_address = Column(String, nullable=False)

    # Fees locked in at REQUEST time from the current ExchangeRate config
    # (Phase 9 addition) — not read live at completion, so an admin
    # changing fees later can't retroactively alter an already-requested
    # withdrawal. The full `amount` above is still what gets frozen/
    # debited from the user's wallet; `net_amount` is what actually gets
    # sent to `destination_address` — the difference is the business's
    # revenue (network fee approximates the real blockchain cost;
    # service fee is Much Exchange's own cut).
    network_fee_amount = Column(Numeric(24, 8), default=0, nullable=False)
    service_fee_amount = Column(Numeric(24, 8), default=0, nullable=False)
    net_amount = Column(Numeric(24, 8), nullable=False)  # = amount - network_fee_amount - service_fee_amount

    status = Column(String, default=WithdrawalStatus.PENDING, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    rejection_action = Column(String, nullable=True)  # "unfreeze" | "keep_frozen" — set only on reject

    admin_tx_hash = Column(String, nullable=True)  # set when admin completes the withdrawal

    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
