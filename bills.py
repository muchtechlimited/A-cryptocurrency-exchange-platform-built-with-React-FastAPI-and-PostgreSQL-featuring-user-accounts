"""
Airtime & data.

Customer
  GET  /bills/config            - networks, airtime limits, and whether buying is available right now
  GET  /bills/plans?network=    - active data plans
  POST /bills/airtime           - buy airtime from the NGN wallet
  POST /bills/data              - buy a data plan from the NGN wallet
  GET  /bills/orders            - my purchases
  GET  /bills/orders/{id}       - one of my purchases

Admin
  GET   /bills/admin/orders                  - all purchases (filter by status)
  POST  /bills/admin/orders/{id}/resolve     - settle a stuck "processing" order by hand
  POST  /bills/admin/orders/{id}/requery     - ask the provider what happened to a stuck order
  GET   /bills/admin/plans                   - data plans incl. cost price and inactive ones
  POST  /bills/admin/plans                   - add a plan
  PATCH /bills/admin/plans/{id}              - edit prices / name / switch on or off
  GET   /bills/admin/provider                - which provider is active and its balance
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_admin, get_current_user
from app.core.rate_limit import limiter
from app.models.bill import BillCategory, BillNetwork, BillOrder, BillOrderStatus, BillPlan
from app.models.user import User
from app.schemas.bill import (
    AirtimePurchase,
    BillConfigOut,
    BillNetworkOut,
    BillOrderAdminOut,
    BillOrderOut,
    BillPlanAdminOut,
    BillPlanCreate,
    BillPlanOut,
    BillPlanUpdate,
    BillProviderOut,
    DataPurchase,
    ResolveOrder,
)
from app.utils import bills_service
from app.utils.audit import log_admin_action
from app.utils.bill_providers import ProviderUnavailable, get_provider

router = APIRouter()


def _provider_ready() -> bool:
    if not settings.BILLS_ENABLED:
        return False
    try:
        return get_provider().is_configured()
    except ProviderUnavailable:
        return False


# ------------------------------------------------------------------ customer
@router.get("/config", response_model=BillConfigOut)
def bills_config(current_user: User = Depends(get_current_user)):
    return BillConfigOut(
        available=_provider_ready(),
        networks=[BillNetworkOut(id=n, label=BillNetwork.LABELS[n]) for n in BillNetwork.ALL],
        airtime_min=settings.BILLS_AIRTIME_MIN_NGN,
        airtime_max=settings.BILLS_AIRTIME_MAX_NGN,
    )


@router.get("/plans", response_model=list[BillPlanOut])
def list_plans(
    network: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(BillPlan).filter(BillPlan.is_active.is_(True))
    if network:
        q = q.filter(BillPlan.network == network)
    return q.order_by(BillPlan.network, BillPlan.selling_price).all()


@router.post("/airtime", response_model=BillOrderOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def buy_airtime(request: Request, payload: AirtimePurchase, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bills_service.purchase(
        db, current_user, category=BillCategory.AIRTIME, network=payload.network, phone=payload.phone,
        client_ref=payload.client_ref, amount=payload.amount,
    )


@router.post("/data", response_model=BillOrderOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def buy_data(request: Request, payload: DataPurchase, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return bills_service.purchase(
        db, current_user, category=BillCategory.DATA, network=payload.network, phone=payload.phone,
        client_ref=payload.client_ref, plan_id=payload.plan_id,
    )


@router.get("/orders", response_model=list[BillOrderOut])
def my_orders(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(BillOrder).filter(BillOrder.user_id == current_user.id).order_by(BillOrder.created_at.desc()).limit(limit).all()


@router.get("/orders/{order_id}", response_model=BillOrderOut)
def my_order(order_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.query(BillOrder).filter(BillOrder.id == order_id, BillOrder.user_id == current_user.id).first()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")  # also for other people's orders
    return order


# ------------------------------------------------------------------ admin
@router.get("/admin/orders", response_model=list[BillOrderAdminOut])
def admin_orders(
    status_filter: str | None = Query(default=None, description="processing | completed | failed"),
    limit: int = Query(default=200, ge=1, le=1000),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    q = db.query(BillOrder)
    if status_filter:
        if status_filter not in BillOrderStatus.ALL:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown status")
        q = q.filter(BillOrder.status == status_filter)
    return q.order_by(BillOrder.created_at.desc()).limit(limit).all()


@router.post("/admin/orders/{order_id}/resolve", response_model=BillOrderAdminOut)
def admin_resolve_order(order_id: uuid.UUID, payload: ResolveOrder, current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    order = bills_service.resolve_order(db, order_id, payload.outcome, current_admin, payload.note)
    log_admin_action(
        db, current_admin.id, "bill_order_resolved", "bill_order",
        f"Settled airtime/data order as {payload.outcome}" + (" and refunded the customer" if payload.outcome == "failed" else ""),
        target_id=order.id, target_user_id=order.user_id, details={"outcome": payload.outcome, "note": payload.note, "amount": str(order.amount)},
    )
    db.commit()
    db.refresh(order)
    return order


@router.post("/admin/orders/{order_id}/requery", response_model=BillOrderAdminOut)
def admin_requery_order(order_id: uuid.UUID, current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    before = db.query(BillOrder.status).filter(BillOrder.id == order_id).scalar()
    order = bills_service.requery_order(db, order_id)
    if before == BillOrderStatus.PROCESSING and order.status != BillOrderStatus.PROCESSING:
        log_admin_action(
            db, current_admin.id, "bill_order_requeried", "bill_order", f"Provider status check settled an order as {order.status}",
            target_id=order.id, target_user_id=order.user_id,
        )
        db.commit()
    return order


@router.get("/admin/plans", response_model=list[BillPlanAdminOut])
def admin_plans(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(BillPlan).order_by(BillPlan.network, BillPlan.selling_price).all()


@router.post("/admin/plans", response_model=BillPlanAdminOut, status_code=status.HTTP_201_CREATED)
def admin_create_plan(payload: BillPlanCreate, current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    if payload.network not in BillNetwork.ALL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown network")
    plan = BillPlan(**payload.model_dump())
    db.add(plan)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A plan with that provider code already exists on this network")
    log_admin_action(db, current_admin.id, "bill_plan_created", "bill_plan", f"Added data plan {BillNetwork.LABELS[plan.network]} {plan.name} at ₦{plan.selling_price:,.0f}", target_id=plan.id)
    db.commit()
    db.refresh(plan)
    return plan


@router.patch("/admin/plans/{plan_id}", response_model=BillPlanAdminOut)
def admin_update_plan(plan_id: uuid.UUID, payload: BillPlanUpdate, current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    plan = db.query(BillPlan).filter(BillPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(plan, k, v)
    log_admin_action(db, current_admin.id, "bill_plan_updated", "bill_plan", f"Updated data plan {BillNetwork.LABELS.get(plan.network, plan.network)} {plan.name}", target_id=plan.id, details={k: str(v) for k, v in changes.items()})
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/admin/provider", response_model=BillProviderOut)
def admin_provider(current_admin: User = Depends(get_current_admin)):
    try:
        provider = get_provider()
    except ProviderUnavailable:
        return BillProviderOut(provider=settings.BILLS_PROVIDER, enabled=settings.BILLS_ENABLED, configured=False)
    balance = None
    try:
        balance = provider.balance() if provider.is_configured() else None
    except Exception:
        balance = None
    return BillProviderOut(provider=provider.name, enabled=settings.BILLS_ENABLED, configured=provider.is_configured(), balance=balance)
