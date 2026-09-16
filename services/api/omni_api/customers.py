from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.audit import add_audit_event
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.database import get_session
from services.api.omni_api.models import Customer, OutboxEvent
from services.api.omni_api.schemas import CustomerCreate, CustomerList, CustomerRead

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=CustomerList)
async def list_customers(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CustomerList:
    filters = [Customer.tenant_id == context.tenant_id]
    if query:
        filters.append(Customer.name.ilike(f"%{query}%"))

    total = await session.scalar(select(func.count(Customer.id)).where(*filters))
    result = await session.scalars(
        select(Customer).where(*filters).order_by(Customer.name, Customer.id).offset(offset).limit(limit)
    )
    return CustomerList(items=[CustomerRead.model_validate(item) for item in result], total=total or 0)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
async def create_customer(
    payload: CustomerCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> Customer:
    customer = Customer(tenant_id=context.tenant_id, **payload.model_dump())
    session.add(customer)
    await session.flush()
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action="customer.created",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        payload={"name": customer.name},
    )
    session.add(
        OutboxEvent(
            tenant_id=context.tenant_id,
            event_type="customer.created",
            aggregate_type="customer",
            aggregate_id=customer.id,
            payload={"name": customer.name},
            occurred_at=datetime.now(timezone.utc),
            correlation_id=request.state.correlation_id,
        )
    )
    await session.commit()
    await session.refresh(customer)
    return customer
