"""Omni Agent tools for retail: catalog, forecasting, insights, restock, campaigns."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.ai_tools import ToolInputError
from services.api.omni_api.auth import TenantContext
from services.api.omni_api.config import Settings
from services.api.omni_api.models import Campaign, Customer, PurchaseOrder, PurchaseOrderLine
from services.api.omni_api.operations import record_change
from services.api.omni_api.retail_analytics import (
    SEGMENT_GUIDE,
    companions,
    customer_profile,
    customer_rfm,
    forecast_with_backend,
    load_dataset,
    segment_summary,
    top_pairs,
)
from services.api.omni_api.retail_shop import local_rank, retrieve

RETAIL_TOOL_NAMES = {
    "search_products",
    "forecast_stockouts",
    "customer_insights",
    "basket_insights",
    "propose_restock_orders",
    "draft_campaign",
}
MAX_RESTOCK_LINES = 10


def money(cents: int) -> str:
    return f"S${cents / 100:,.2f}"


def clamp_horizon(value: Any) -> int:
    try:
        return min(60, max(7, int(value or 14)))
    except (TypeError, ValueError):
        return 14


def parse_lines(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise ToolInputError("Restock lines must be a JSON list of {sku, quantity}") from None
    if not isinstance(value, list):
        raise ToolInputError("Restock lines must be a list")
    lines = []
    for item in value:
        if not isinstance(item, dict) or not item.get("sku"):
            raise ToolInputError("Each restock line needs a SKU")
        try:
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ToolInputError(f"Quantity for {item['sku']} must be a whole number") from None
        if quantity < 0 or quantity > 100_000:
            raise ToolInputError(f"Quantity for {item['sku']} is out of range")
        lines.append({**item, "sku": str(item["sku"]).strip(), "quantity": quantity})
    return lines


async def prepare_retail_arguments(
    name: str, arguments: dict[str, Any], *, session: AsyncSession, context: TenantContext, settings: Settings
) -> dict[str, Any]:
    """Ground proposals in data before review: quantities come from the forecast, not the model."""
    if name == "propose_restock_orders":
        horizon = clamp_horizon(arguments.get("horizon_days"))
        dataset = await load_dataset(session, context.tenant_id)
        forecast = {row["sku"]: row for row in await forecast_with_backend(dataset, settings, horizon)}
        requested = parse_lines(arguments.get("lines"))
        if requested:
            chosen = [(forecast[line["sku"]], line["quantity"]) for line in requested if line["sku"] in forecast]
        else:
            chosen = [
                (row, row["recommended_quantity"])
                for row in forecast.values()
                if row["risk"] in ("critical", "high") and row["recommended_quantity"] > 0
            ]
        lines = [
            {
                "sku": row["sku"],
                "name": row["name"],
                "quantity": quantity or row["recommended_quantity"],
                "on_hand": row["on_hand"],
                "forecast_units": row["forecast_units"],
                "stockout_date": row["stockout_date"],
                "risk": row["risk"],
                "supplier": row["supplier"],
            }
            for row, quantity in chosen[:MAX_RESTOCK_LINES]
        ]
        return {**arguments, "lines": lines, "horizon_days": horizon}
    if name == "draft_campaign":
        dataset = await load_dataset(session, context.tenant_id)
        segment = str(arguments.get("segment") or "")
        audience = sum(1 for entry in customer_rfm(dataset).values() if entry["segment"] == segment)
        return {**arguments, "audience_size": audience}
    return arguments


async def execute_retail_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    context: TenantContext,
    request: Request,
    idempotency_key: str,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    dataset = await load_dataset(session, context.tenant_id)
    if not dataset.products and name != "customer_insights":
        raise ToolInputError("This workspace has no retail catalog yet")

    if name == "search_products":
        query = str(arguments.get("query") or "").strip()
        products, backend = await retrieve(settings, dataset, query)
        products = (products or local_rank(dataset, query))[:6]
        cards = [product.card() for product in products]
        listed = ", ".join(
            f"{product.name} ({money(product.price_cents)}, {product.on_hand} in stock)" for product in products[:4]
        )
        return {
            "summary": f"Found {len(products)} products: {listed}." if products else "No catalog products matched.",
            "data": {"products": cards, "search_backend": backend},
            "links": [{"label": product.name, "resource": "product", "id": product.id} for product in products],
            "citations": [],
        }

    if name == "forecast_stockouts":
        horizon = clamp_horizon(arguments.get("horizon_days"))
        rows = await forecast_with_backend(dataset, settings, horizon)
        at_risk = [row for row in rows if row["risk"] in ("critical", "high")]
        source = "BigQuery AI.FORECAST" if rows and rows[0]["source"] == "bigquery_ai_forecast" else "seasonal model"
        details = "; ".join(
            f"{row['name']} ({row['on_hand']} on hand, ~{row['days_of_cover']} days cover, "
            f"stock-out {row['stockout_date']}, reorder {row['recommended_quantity']})"
            for row in at_risk[:5]
        )
        summary = (
            f"{len(at_risk)} products are forecast to stock out within {horizon} days plus supplier lead time "
            f"({source}): {details}."
            if at_risk
            else f"No products are forecast to stock out in the next {horizon} days ({source})."
        )
        return {
            "summary": summary,
            "data": {"horizon_days": horizon, "products": at_risk[:10], "source": source},
            "links": [],
            "citations": [],
        }

    if name == "customer_insights":
        customer_name = str(arguments.get("customer_name") or "").strip()
        if customer_name:
            customer = await session.scalar(
                select(Customer)
                .where(Customer.tenant_id == context.tenant_id, Customer.name.ilike(f"%{customer_name}%"))
                .limit(1)
            )
            if customer is None:
                raise ToolInputError(f'No customer matching "{customer_name}" was found')
            profile = customer_profile(dataset, customer.id)
            profile["name"] = customer.name
            last = (
                f"last order {profile['recency_days']} days ago" if profile["recency_days"] is not None else "no orders"
            )
            favourites = ", ".join(profile["favorite_categories"]) or "none yet"
            return {
                "summary": (
                    f"{customer.name} is in the {profile['segment']} segment: {profile['orders']} orders, "
                    f"{money(profile['lifetime_value_cents'])} spent, {last}. Favourite categories: {favourites}."
                ),
                "data": {"profile": profile},
                "links": [{"label": customer.name, "resource": "customer", "id": customer.id}],
                "citations": [],
            }
        segments = segment_summary(dataset)
        wanted = str(arguments.get("segment") or "").strip().lower()
        if wanted:
            segments = [row for row in segments if row["segment"].lower() == wanted] or segments
        summary = "; ".join(
            f"{row['segment']}: {row['customers']} customers, {money(row['revenue_cents'])} revenue"
            + (
                f", last order ~{row['avg_recency_days']} days ago on average, mostly buying "
                f"{', '.join(row['top_categories']) or 'n/a'}. Suggested action: {row['recommended_action']}"
                if wanted
                else ""
            )
            for row in segments
        )
        return {"summary": summary.rstrip(".") + ".", "data": {"segments": segments}, "links": [], "citations": []}

    if name == "basket_insights":
        reference = arguments.get("product")
        product = dataset.find_product(reference) if reference else None
        if reference and product is None:
            matches = local_rank(dataset, str(reference))
            product = matches[0] if matches else None
        if product:
            rules = companions(dataset, product.id)
            summary = (
                f"Shoppers who buy {product.name} also buy "
                + ", ".join(f"{rule['companion_name']} ({rule['confidence']:.0%} of baskets)" for rule in rules)
                + "."
                if rules
                else f"No strong basket pairings for {product.name} yet."
            )
            return {
                "summary": summary,
                "data": {"product": product.card(), "rules": rules},
                "links": [],
                "citations": [],
            }
        pairs = top_pairs(dataset, limit=8)
        summary = "Strongest product pairings: " + "; ".join(
            f"{pair['anchor_name']} + {pair['companion_name']} (lift {pair['lift']})" for pair in pairs[:5]
        )
        return {"summary": summary + ".", "data": {"pairs": pairs}, "links": [], "citations": []}

    if name == "propose_restock_orders":
        existing = await session.scalar(
            select(func.count(PurchaseOrder.id)).where(
                PurchaseOrder.tenant_id == context.tenant_id,
                PurchaseOrder.idempotency_key.like(f"{idempotency_key}%"),
            )
        )
        if existing:
            return {"summary": "These purchase orders were already created.", "data": {}, "links": [], "citations": []}
        lines = [line for line in parse_lines(arguments.get("lines")) if line["quantity"] > 0]
        if not lines:
            raise ToolInputError("No restock lines to order")
        by_sku = dataset.by_sku
        unknown = [line["sku"] for line in lines if line["sku"] not in by_sku]
        if unknown:
            raise ToolInputError(f"Unknown SKU: {', '.join(unknown)}")
        by_supplier: dict[str, list[dict[str, Any]]] = {}
        for line in lines[:MAX_RESTOCK_LINES]:
            by_supplier.setdefault(by_sku[line["sku"]].supplier or "Unassigned supplier", []).append(line)
        count = await session.scalar(
            select(func.count(PurchaseOrder.id)).where(PurchaseOrder.tenant_id == context.tenant_id)
        )
        today = datetime.now(timezone.utc)
        created = []
        for index, (supplier, supplier_lines) in enumerate(sorted(by_supplier.items())):
            total = sum(by_sku[line["sku"]].cost_cents * line["quantity"] for line in supplier_lines)
            order = PurchaseOrder(
                tenant_id=context.tenant_id,
                number=f"PO-{today:%y%m%d}-{(count or 0) + index + 1:04d}",
                status="approved",
                source="omni_agent",
                total_cost_cents=total,
                currency=next(iter(dataset.products.values())).currency,
                note=arguments.get("note"),
                forecast={
                    "horizon_days": clamp_horizon(arguments.get("horizon_days")),
                    "lines": [
                        {key: line.get(key) for key in ("sku", "forecast_units", "stockout_date", "risk")}
                        for line in supplier_lines
                    ],
                },
                created_by_user_id=context.user.id,
                idempotency_key=f"{idempotency_key}-{index}",
            )
            order.lines = [
                PurchaseOrderLine(
                    tenant_id=context.tenant_id,
                    product_id=by_sku[line["sku"]].id,
                    supplier=supplier,
                    quantity=line["quantity"],
                    unit_cost_cents=by_sku[line["sku"]].cost_cents,
                )
                for line in supplier_lines
            ]
            session.add(order)
            await session.flush()
            record_change(
                session,
                context=context,
                request=request,
                event_type="purchase_order.approved",
                resource_type="purchase_order",
                resource_id=order.id,
                payload={"number": order.number, "supplier": supplier, "total_cost_cents": total},
            )
            created.append(order)
        await session.commit()
        units = sum(line["quantity"] for line in lines)
        total_cost = sum(order.total_cost_cents for order in created)
        return {
            "summary": (
                f"Created {len(created)} approved purchase order{'s' if len(created) != 1 else ''} "
                f"for {units} units ({money(total_cost)}): " + ", ".join(order.number for order in created) + "."
            ),
            "data": {
                "purchase_orders": [
                    {"id": order.id, "number": order.number, "total_cost_cents": order.total_cost_cents}
                    for order in created
                ]
            },
            "links": [{"label": order.number, "resource": "purchase_order", "id": order.id} for order in created],
            "citations": [],
        }

    if name == "draft_campaign":
        segment = str(arguments.get("segment") or "")
        if segment not in SEGMENT_GUIDE:
            raise ToolInputError(f"Segment must be one of: {', '.join(SEGMENT_GUIDE)}")
        channel = arguments.get("channel") or "email"
        if channel not in ("email", "sms"):
            raise ToolInputError("Channel must be email or sms")
        audience = sum(1 for entry in customer_rfm(dataset).values() if entry["segment"] == segment)
        campaign = Campaign(
            tenant_id=context.tenant_id,
            segment=segment,
            offer=str(arguments["offer"]),
            message=str(arguments["message"]),
            channel=channel,
            audience_size=audience,
            status="approved",
            created_by_user_id=context.user.id,
            idempotency_key=idempotency_key,
        )
        session.add(campaign)
        await session.flush()
        record_change(
            session,
            context=context,
            request=request,
            event_type="campaign.approved",
            resource_type="campaign",
            resource_id=campaign.id,
            payload={"segment": segment, "channel": channel, "audience_size": audience},
        )
        await session.commit()
        return {
            "summary": f"Approved a {channel} campaign for {audience} {segment} customers: “{campaign.offer}”.",
            "data": {"campaign_id": campaign.id, "audience_size": audience},
            "links": [{"label": f"{segment} campaign", "resource": "campaign", "id": campaign.id}],
            "citations": [],
        }

    raise ToolInputError("Tool is not executable")
