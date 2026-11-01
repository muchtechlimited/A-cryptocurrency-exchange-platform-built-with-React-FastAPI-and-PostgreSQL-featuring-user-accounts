"""
Order model — buy/sell orders.

Lifecycle:
  1. Created with status=PENDING, and the paying-side amount is frozen
     on the user's wallet (NGN for a buy, crypto for a sell) at
     placement time — see app/routers/trading.py.
  2. Admin reviews. On approve: the frozen amount is debited and the
     received-side wallet is credited, both logged as Transactions.
  3. On reject: admin explicitly chooses whether to unfreeze the held
     amount or leave it frozen for manual handling — see
     `rejection_action` below. This is deliberately NOT automatic
     (confirmed decision, see roadmap).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class OrderSide:
    BUY = "buy"
    SELL = "sell"
    ALL = [BUY, SELL]


class OrderStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALL = [PENDING, APPROVED, REJECTED]


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    side = Column(String, nullable=False)      # "buy" | "sell"
    currency = Column(String, nullable=False)  # "BTC" | "USDT" — the crypto side of the trade

    crypto_amount = Column(Numeric(24, 8), nullable=False)
    rate_applied = Column(Numeric(24, 8), nullable=False)   # NGN per unit, locked in at order placement
    fee_percentage = Column(Numeric(6, 3), default=0, nullable=False)
    fee_amount_ngn = Column(Numeric(24, 8), default=0, nullable=False)
    ngn_amount = Column(Numeric(24, 8), nullable=False)     # gross NGN value at rate_applied, before fee
    net_ngn_amount = Column(Numeric(24, 8), nullable=False)
    # buy:  net_ngn_amount = ngn_amount + fee  (total the user pays)
    # sell: net_ngn_amount = ngn_amount - fee  (total the user receives)

    status = Column(String, default=OrderStatus.PENDING, nullable=False)

    # What was frozen at placement time, so approval/rejection knows
    # exactly how much to release/debit regardless of currency:
    # buy  -> NGN wallet frozen by net_ngn_amount
    # sell -> crypto wallet frozen by crypto_amount
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    rejection_action = Column(String, nullable=True)  # "unfreeze" | "keep_frozen" — set only on reject

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
