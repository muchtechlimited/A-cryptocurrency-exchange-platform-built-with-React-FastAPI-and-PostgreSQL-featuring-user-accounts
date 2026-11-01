from datetime import datetime

from pydantic import BaseModel


class MarketCoin(BaseModel):
    symbol: str
    name: str
    price_ngn: float
    # None when the feed gave a price but no 24h figure - the UI shows "-", never 0%.
    change_24h_pct: float | None = None


class MarketOverviewOut(BaseModel):
    available: bool
    stale: bool
    as_of: datetime | None = None
    source: str
    coins: list[MarketCoin]
