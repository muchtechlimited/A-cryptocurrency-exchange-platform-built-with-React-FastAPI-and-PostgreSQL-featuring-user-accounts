"""
Exchange rate routes.

Minimal version pulled forward from Phase 7 (admin Rates page) — just
enough for Phase 5 trading to function. Phase 7 will likely expand
this (rate history, multiple sources, etc.) but the table and these
endpoints don't need to change shape for that.

  GET /rates              - view current rates (any logged-in user — needed before placing an order)
  PUT /rates/admin/{currency}  - admin sets/updates the rate for a currency
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user_or_admin
from app.models.exchange_rate import ExchangeRate
from app.models.user import User
from app.schemas.trading import ExchangeRateOut, ExchangeRateUpdate
from app.utils.audit import log_admin_action

router = APIRouter()


@router.get("", response_model=list[ExchangeRateOut])
def list_rates(
    current_user: User = Depends(get_current_user_or_admin),
    db: Session = Depends(get_db),
):
    rates = db.query(ExchangeRate).all()
    return [ExchangeRateOut.model_validate(r) for r in rates]


@router.put("/admin/{currency}", response_model=ExchangeRateOut)
def set_rate(
    currency: str,
    payload: ExchangeRateUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rate = db.query(ExchangeRate).filter(ExchangeRate.currency == currency).first()
    is_new = rate is None
    before = None if is_new else {
        "buy_rate_ngn": float(rate.buy_rate_ngn), "sell_rate_ngn": float(rate.sell_rate_ngn),
        "buy_fee_percentage": float(rate.buy_fee_percentage), "sell_fee_percentage": float(rate.sell_fee_percentage),
    }
    if rate is None:
        rate = ExchangeRate(currency=currency)
        db.add(rate)

    rate.buy_rate_ngn = payload.buy_rate_ngn
    rate.sell_rate_ngn = payload.sell_rate_ngn
    rate.buy_fee_percentage = payload.buy_fee_percentage
    rate.sell_fee_percentage = payload.sell_fee_percentage
    rate.withdrawal_network_fee = payload.withdrawal_network_fee
    rate.withdrawal_service_fee = payload.withdrawal_service_fee
    rate.updated_by_admin_id = current_admin.id

    log_admin_action(
        db, current_admin.id,
        action="rate.created" if is_new else "rate.updated",
        target_type="exchange_rate", target_id=currency,
        summary=f"{'Set' if is_new else 'Updated'} {currency} rate — buy {payload.buy_rate_ngn} / sell {payload.sell_rate_ngn} NGN",
        details={"before": before, "after": {
            "buy_rate_ngn": float(payload.buy_rate_ngn), "sell_rate_ngn": float(payload.sell_rate_ngn),
            "buy_fee_percentage": float(payload.buy_fee_percentage), "sell_fee_percentage": float(payload.sell_fee_percentage),
        }},
    )

    db.commit()
    db.refresh(rate)
    return ExchangeRateOut.model_validate(rate)
