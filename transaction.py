"""
Transaction model — the ledger.

Every single balance change anywhere in the app (deposit, withdrawal,
buy, sell, or a manual admin credit/debit) must create one of these.
Nothing should ever mutate a wallet balance without a matching row here
— that's what makes the Transactions pages (user + admin) trustworthy,
and it's what lets support/disputes be resolved by reading history
instead of guessing.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class TransactionType:
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BUY_BTC = "buy_btc"
    SELL_BTC = "sell_btc"
    BUY_USDT = "buy_usdt"
    SELL_USDT = "sell_usdt"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    GIFT_CARD_SELL = "gift_card_sell"
    BUY_GIFT_CARD = "buy_gift_card"
    REFERRAL_REWARD = "referral_reward"
    PROMOTION_REWARD = "promotion_reward"
    BILL_AIRTIME = "bill_airtime"
    BILL_DATA = "bill_data"
    BILL_REFUND = "bill_refund"
    ALL = [DEPOSIT, WITHDRAWAL, BUY_BTC, SELL_BTC, BUY_USDT, SELL_USDT, ADMIN_CREDIT, ADMIN_DEBIT, GIFT_CARD_SELL, BUY_GIFT_CARD, REFERRAL_REWARD, PROMOTION_REWARD, BILL_AIRTIME, BILL_DATA, BILL_REFUND]


class TransactionStatus:
    PENDING = "pending"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ALL = [PENDING, COMPLETED, REJECTED]


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True)

    type = Column(String, nullable=False)      # one of TransactionType.ALL
    status = Column(String, default=TransactionStatus.COMPLETED, nullable=False)

    currency = Column(String, nullable=False)  # "NGN" | "BTC" | "USDT"
    amount = Column(Numeric(24, 8), nullable=False)  # always positive; `type` implies credit vs debit direction

    # Running balance snapshots AFTER this transaction applied — makes it
    # possible to reconstruct/audit history without replaying every row.
    balance_after = Column(Numeric(24, 8), nullable=True)

    # Optional fee charged as part of this transaction (Phase 5 buy/sell
    # preview feature — see roadmap). Null/0 when no fee applies.
    fee_amount = Column(Numeric(24, 8), default=0, nullable=False)

    reference = Column(String, nullable=True, index=True)  # e.g. TxID for crypto deposits/withdrawals, Paystack ref for NGN
    description = Column(Text, nullable=True)

    # Set when an admin action (credit/debit, or approving a deposit/
    # withdrawal/order) caused this transaction. Null for purely
    # automatic/system-generated rows.
    created_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
