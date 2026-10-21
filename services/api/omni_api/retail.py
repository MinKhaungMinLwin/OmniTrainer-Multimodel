from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.api.omni_api.auth import TenantContext, get_tenant_context
from services.api.omni_api.database import get_session
from services.api.omni_api.models import Campaign, Customer, Product, PurchaseOrder, SalesOrder
from services.api.omni_api.retail_analytics import (
    SEGMENT_GUIDE,
    companions,
    customer_profile,
    customer_rfm,
    forecast_with_backend,
    load_dataset,
    overview,
    personalised_picks,
    segment_summary,
    top_pairs,
)
from services.api.omni_api.retail_shop import ShopAssistRequest, assist, local_rank

router = APIRouter(prefix="/retail", tags=["retail"])


@router.get("/config")
async def retail_config(request: Request, context: TenantContext = Depends(get_tenant_context)) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "currency": settings.retail_currency,
        "ai_provider": settings.ai_provider,
        "ai_model": settings.ai_model,
        "search_backend": "vertex_ai_search" if settings.retail_search_backend == "vertex" else "local",
        "analytics_backend": "bigquery" if settings.retail_analytics_backend == "bigquery" else "local",
        "looker_embed_url": settings.looker_embed_url,
    }


@router.get("/products")
async def list_products(
    request: Request,
    query: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=120),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    dataset = await load_dataset(session, context.tenant_id, history_days=1)
    products = local_rank(dataset, query) if query else sorted(dataset.products.values(), key=lambda p: p.name)
    if category:
        products = [product for product in products if product.category == category]
    return [
        {
            **product.card(),
            "cost_cents": product.cost_cents,
            "on_order": product.on_order,
            "lead_time_days": product.lead_time_days,
            "supplier": product.supplier,
        }
        for product in products
    ]


@router.get("/orders")
async def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    orders = await session.scalars(
        select(SalesOrder)
        .options(selectinload(SalesOrder.lines))
        .where(SalesOrder.tenant_id == context.tenant_id)
        .order_by(SalesOrder.placed_at.desc())
        .limit(limit)
    )
    orders = list(orders)
    customer_ids = {order.customer_id for order in orders if order.customer_id}
    product_ids = {line.product_id for order in orders for line in order.lines}
    names = dict((await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids)))).all())
    products = dict((await session.execute(select(Product.id, Product.name).where(Product.id.in_(product_ids)))).all())
    return [
        {
            "id": order.id,
            "number": order.number,
            "customer_id": order.customer_id,
            "customer_name": names.get(order.customer_id) if order.customer_id else None,
            "channel": order.channel,
            "store": order.store,
            "placed_at": order.placed_at.isoformat(),
            "total_cents": order.total_cents,
            "discount_cents": order.discount_cents,
            "currency": order.currency,
            "items": [
                {"name": products.get(line.product_id, "Unknown"), "quantity": line.quantity} for line in order.lines
            ],
        }
        for order in orders
    ]


@router.get("/insights/overview")
async def insights_overview(
    context: TenantContext = Depends(get_tenant_context), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return overview(await load_dataset(session, context.tenant_id))


@router.get("/insights/inventory")
async def insights_inventory(
    request: Request,
    horizon_days: int = Query(default=14, ge=7, le=60),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    dataset = await load_dataset(session, context.tenant_id)
    return await forecast_with_backend(dataset, request.app.state.settings, horizon_days)


@router.get("/insights/segments")
async def insights_segments(
    context: TenantContext = Depends(get_tenant_context), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    return segment_summary(await load_dataset(session, context.tenant_id))


@router.get("/insights/basket")
async def insights_basket(
    product_id: str | None = None,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    dataset = await load_dataset(session, context.tenant_id)
    if product_id:
        if product_id not in dataset.products:
            raise HTTPException(status_code=404, detail="Product not found")
        return companions(dataset, product_id, limit=6)
    return top_pairs(dataset)


@router.get("/shoppers")
async def list_shoppers(
    context: TenantContext = Depends(get_tenant_context), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """A few representative customers per segment for the storefront's "shop as" picker."""
    dataset = await load_dataset(session, context.tenant_id)
    rfm = customer_rfm(dataset)
    shoppers = []
    for segment in SEGMENT_GUIDE:
        members = sorted(
            (item for item in rfm.items() if item[1]["segment"] == segment), key=lambda item: -item[1]["monetary"]
        )
        for customer_id, entry in members[:3]:
            shoppers.append(
                {
                    "id": customer_id,
                    "name": dataset.customers.get(customer_id, "Customer"),
                    "segment": segment,
                    "orders": entry["frequency"],
                    "favorite_category": entry["categories"].most_common(1)[0][0] if entry["categories"] else None,
                }
            )
    return shoppers


@router.get("/customers/{customer_id}/profile")
async def retail_customer_profile(
    customer_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    customer = await session.scalar(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == context.tenant_id)
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    dataset = await load_dataset(session, context.tenant_id)
    profile = customer_profile(dataset, customer_id)
    profile["name"] = customer.name
    return profile


@router.get("/shop/recommendations")
async def shop_recommendations(
    customer_id: str | None = None,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    dataset = await load_dataset(session, context.tenant_id)
    if customer_id and not await tenant_customer_exists(session, context.tenant_id, customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    return personalised_picks(dataset, customer_id)


@router.post("/shop/assist")
async def shop_assist(
    payload: ShopAssistRequest,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if payload.customer_id and not await tenant_customer_exists(session, context.tenant_id, payload.customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    dataset = await load_dataset(session, context.tenant_id)
    if not dataset.products:
        raise HTTPException(status_code=409, detail="This workspace has no retail catalog yet")
    return await assist(
        request.app.state.settings, dataset, payload, tenant_id=context.tenant_id, user_id=context.user.id
    )


@router.get("/purchase-orders")
async def list_purchase_orders(
    context: TenantContext = Depends(get_tenant_context), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    orders = list(
        await session.scalars(
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.lines))
            .where(PurchaseOrder.tenant_id == context.tenant_id)
            .order_by(PurchaseOrder.created_at.desc())
            .limit(50)
        )
    )
    product_ids = {line.product_id for order in orders for line in order.lines}
    products = dict((await session.execute(select(Product.id, Product.name).where(Product.id.in_(product_ids)))).all())
    return [
        {
            "id": order.id,
            "number": order.number,
            "status": order.status,
            "source": order.source,
            "supplier": order.lines[0].supplier if order.lines else None,
            "total_cost_cents": order.total_cost_cents,
            "currency": order.currency,
            "note": order.note,
            "created_at": order.created_at.isoformat(),
            "lines": [
                {
                    "product_id": line.product_id,
                    "name": products.get(line.product_id, "Unknown"),
                    "quantity": line.quantity,
                    "unit_cost_cents": line.unit_cost_cents,
                }
                for line in order.lines
            ],
        }
        for order in orders
    ]


@router.get("/campaigns")
async def list_campaigns(
    context: TenantContext = Depends(get_tenant_context), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    campaigns = await session.scalars(
        select(Campaign).where(Campaign.tenant_id == context.tenant_id).order_by(Campaign.created_at.desc()).limit(50)
    )
    return [
        {
            "id": campaign.id,
            "segment": campaign.segment,
            "offer": campaign.offer,
            "message": campaign.message,
            "channel": campaign.channel,
            "audience_size": campaign.audience_size,
            "status": campaign.status,
            "created_at": campaign.created_at.isoformat(),
        }
        for campaign in campaigns
    ]


async def tenant_customer_exists(session: AsyncSession, tenant_id: str, customer_id: str) -> bool:
    found = await session.scalar(select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.id == customer_id))
    return found is not None
