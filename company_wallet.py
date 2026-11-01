"""
Company wallet address model.

One row per crypto currency (BTC, USDT) holding the SINGLE shared
address users are shown when they click "Deposit". This is not a
per-user generated address — no HD wallet / address derivation is in
scope. An admin sets/updates this manually (e.g. when rotating to a
new cold wallet).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class CompanyWalletAddress(Base):
    __tablename__ = "company_wallet_addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    currency = Column(String, unique=True, nullable=False)  # "BTC" | "USDT"
    address = Column(String, nullable=False)
    network_note = Column(String, nullable=True)  # e.g. "USDT-TRC20" vs "USDT-ERC20" — matters a lot in practice

    updated_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
