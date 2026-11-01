"""
NGN withdrawal request model.

Unlike crypto withdrawals, this always pays out to the user's own
registered bank account (bank_name/account_number/account_name, collected
at signup and required — every user has exactly one). There's no
"add/select a bank account" step because the schema only ever supports one
account per user, consistent with the rest of the app.

Flow: user requests an amount, which is frozen immediately (same
freeze-then-review pattern as crypto withdrawals and Phase 5 orders).
Unlike crypto withdrawals — which have a separate "approved" state while
waiting for the admin to actually broadcast the transaction and get back a
TxID — a bank transfer has no equivalent client-verifiable confirmation
step the system can wait on, so approval finalizes the debit directly in
one step (mirrors the existing NGN bank-transfer *deposit* flow, which is
also single-step). Admin can optionally record a `admin_reference` (e.g.
their own bank's transfer reference) for the audit trail when approving.

Rejection mirrors the crypto pattern: admin explicitly chooses "unfreeze"
or "keep_frozen" — never automatic.

No withdrawal fee is charged today (`service_fee_amount` is always 0) —
there's no NGN row in the ExchangeRate table the way there is for BTC/USDT,
and adding a configurable NGN fee is a separate product decision. The
field is kept on the model so a fee can be introduced later without a
schema change.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class NGNWithdrawalRequest(Base):
    __tablename__ = "ngn_withdrawal_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    amount = Column(Numeric(24, 8), nullable=False)
    service_fee_amount = Column(Numeric(24, 8), default=0, nullable=False)
    net_amount = Column(Numeric(24, 8), nullable=False)  # = amount - service_fee_amount

    # Snapshotted from the user's profile at request time, not read live —
    # if the user later changes their bank details, an already-submitted
    # request should still show (and pay out to) what was on file when it
    # was made.
    destination_bank_name = Column(String, nullable=False)
    destination_account_number = Column(String, nullable=False)
    destination_account_name = Column(String, nullable=False)

    status = Column(String, default="pending", nullable=False)
    rejection_reason = Column(Text, nullable=True)
    rejection_action = Column(String, nullable=True)  # "unfreeze" | "keep_frozen" — set only on reject
    admin_reference = Column(String, nullable=True)  # optional, set on approval — e.g. the admin's own bank transfer reference

    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
