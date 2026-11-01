"""
Exchange rate model.

Pulled forward from Phase 7 (admin Rates page) because Phase 5 trading
can't calculate a buy/sell order without a current rate. This is
intentionally minimal — just enough for trading to work. The full
admin Rates page (history, multiple rate sources, etc.) is still
Phase 7's job; this just gives it a table to build on top of.

One row per tradeable crypto currency (BTC, USDT) — NGN doesn't need
a rate against itself.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    currency = Column(String, unique=True, nullable=False)  # "BTC" | "USDT"

    # NGN price of 1 unit of this currency, for each direction.
    # Buy rate is what a user pays in NGN to acquire 1 unit; sell rate is
    # what a user receives in NGN for selling 1 unit. Kept separate
    # (rather than one rate) since exchanges typically have a spread.
    buy_rate_ngn = Column(Numeric(24, 8), nullable=False)
    sell_rate_ngn = Column(Numeric(24, 8), nullable=False)

    # Fee percentage applied on top of the rate calculation, e.g. 0.5
    # means 0.5%. Defaults to 0 until an admin sets otherwise — see the
    # Phase 4 roadmap note on the fee assumption.
    buy_fee_percentage = Column(Numeric(6, 3), default=0, nullable=False)
    sell_fee_percentage = Column(Numeric(6, 3), default=0, nullable=False)

    # Withdrawal fees (Phase 9 addition) — unlike the buy/sell fees
    # above, these are FLAT amounts denominated in the currency itself
    # (e.g. BTC, not NGN), not a percentage. Network fee approximates
    # the real blockchain transaction cost; service fee is Much
    # Exchange's own cut. Both admin-configurable per currency, same
    # pattern as the rates above. Locked in at withdrawal REQUEST time
    # (see CryptoWithdrawalRequest) so a fee change can't retroactively
    # alter an already-requested withdrawal.
    withdrawal_network_fee = Column(Numeric(24, 8), default=0, nullable=False)
    withdrawal_service_fee = Column(Numeric(24, 8), default=0, nullable=False)

    updated_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
