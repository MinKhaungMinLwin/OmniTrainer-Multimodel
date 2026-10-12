"""Retail analytics computed from operational data.

The local engine is deterministic and needs no cloud services, so it backs
development, tests, and any deployment where BigQuery has not been synced yet.
When OMNI_RETAIL_ANALYTICS_BACKEND=bigquery, demand forecasts come from
BigQuery AI.FORECAST and every other metric keeps the same contract.
"""

import asyncio
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.config import Settings
from services.api.omni_api.models import (
    Customer,
    InventoryLevel,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)

SINGAPORE = ZoneInfo("Asia/Singapore")
HISTORY_DAYS = 180

SEGMENT_GUIDE: dict[str, tuple[str, str]] = {
    "Champions": ("Recent, frequent, high-spend shoppers.", "Reward with early access and referral perks."),
    "Loyal": ("Buy regularly across the last two months.", "Offer bundles and loyalty-point boosters."),
    "Promising": ("Recent shoppers with two or three orders.", "Personalised cross-sell from their categories."),
    "New": ("First purchase in the last 45 days.", "Welcome series with category tips."),
    "At risk": ("Regular buyers who have gone quiet for 45-120 days.", "Replenishment reminder with a small offer."),
    "Lapsed high-value": ("Top-30% lifetime spend but no order for 75+ days.", "Personal win-back offer."),
    "Hibernating": ("Low, infrequent spend long ago.", "Low-cost seasonal reminders only."),
}


def local_day(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SINGAPORE).date()


def today_local() -> date:
    return datetime.now(SINGAPORE).date()


@dataclass
class ProductInfo:
    id: str
    sku: str
    name: str
    brand: str
    category: str
    description: str
    price_cents: int
    cost_cents: int
    currency: str
    tags: list[str]
    attributes: dict[str, Any]
    on_hand: int = 0
    safety_stock: int = 0
    lead_time_days: int = 7
    case_pack: int = 1
    supplier: str = ""
    on_order: int = 0

    def card(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "brand": self.brand,
            "category": self.category,
            "description": self.description,
            "price_cents": self.price_cents,
            "currency": self.currency,
            "tags": self.tags,
            "attributes": self.attributes,
            "on_hand": self.on_hand,
            "in_stock": self.on_hand > 0,
        }


@dataclass
class OrderInfo:
    id: str
    number: str
    customer_id: str | None
    day: date
    placed_at: datetime
    channel: str
    store: str
    total_cents: int
    items: dict[str, int] = field(default_factory=dict)
    revenue_by_product: dict[str, int] = field(default_factory=dict)


@dataclass
class RetailDataset:
    today: date
    products: dict[str, ProductInfo]
    orders: list[OrderInfo]
    customers: dict[str, str]

    @property
    def by_sku(self) -> dict[str, ProductInfo]:
        return {product.sku: product for product in self.products.values()}

    def find_product(self, reference: str | None) -> ProductInfo | None:
        if not reference:
            return None
        value = reference.strip().lower()
        for product in self.products.values():
            if value in (product.id.lower(), product.sku.lower(), product.name.lower()):
                return product
        matches = [product for product in self.products.values() if value in product.name.lower()]
        return matches[0] if len(matches) == 1 else None


async def load_dataset(session: AsyncSession, tenant_id: str, history_days: int = HISTORY_DAYS) -> RetailDataset:
    today = today_local()
    products: dict[str, ProductInfo] = {}
    rows = await session.execute(
        select(Product, InventoryLevel)
        .outerjoin(InventoryLevel, InventoryLevel.product_id == Product.id)
        .where(Product.tenant_id == tenant_id, Product.is_active.is_(True))
    )
    for product, inventory in rows:
        info = ProductInfo(
            id=product.id,
            sku=product.sku,
            name=product.name,
            brand=product.brand,
            category=product.category,
            description=product.description,
            price_cents=product.price_cents,
            cost_cents=product.cost_cents,
            currency=product.currency,
            tags=list(product.tags or []),
            attributes=dict(product.attributes or {}),
        )
        if inventory is not None:
            info.on_hand = inventory.on_hand
            info.safety_stock = inventory.safety_stock
            info.lead_time_days = inventory.lead_time_days
            info.case_pack = inventory.case_pack
            info.supplier = inventory.supplier
        products[product.id] = info

    open_lines = await session.execute(
        select(PurchaseOrderLine.product_id, PurchaseOrderLine.quantity)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .where(PurchaseOrder.tenant_id == tenant_id, PurchaseOrder.status == "approved")
    )
    for product_id, quantity in open_lines:
        if product_id in products:
            products[product_id].on_order += quantity

    since = datetime.combine(today - timedelta(days=history_days), datetime.min.time(), SINGAPORE)
    orders: dict[str, OrderInfo] = {}
    order_rows = await session.execute(
        select(
            SalesOrder.id,
            SalesOrder.number,
            SalesOrder.customer_id,
            SalesOrder.placed_at,
            SalesOrder.channel,
            SalesOrder.store,
            SalesOrder.total_cents,
        ).where(SalesOrder.tenant_id == tenant_id, SalesOrder.placed_at >= since)
    )
    for order_id, number, customer_id, placed_at, channel, store, total in order_rows:
        orders[order_id] = OrderInfo(
            order_id, number, customer_id, local_day(placed_at), placed_at, channel, store, total
        )
    line_rows = await session.execute(
        select(
            SalesOrderLine.order_id,
            SalesOrderLine.product_id,
            SalesOrderLine.quantity,
            SalesOrderLine.line_total_cents,
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
        .where(SalesOrderLine.tenant_id == tenant_id, SalesOrder.placed_at >= since)
    )
    for order_id, product_id, quantity, line_total in line_rows:
        order = orders.get(order_id)
        if order is None:
            continue
        order.items[product_id] = order.items.get(product_id, 0) + quantity
        order.revenue_by_product[product_id] = order.revenue_by_product.get(product_id, 0) + line_total

    customer_ids = {order.customer_id for order in orders.values() if order.customer_id}
    customers: dict[str, str] = {}
    if customer_ids:
        for customer_id, name in await session.execute(
            select(Customer.id, Customer.name).where(Customer.tenant_id == tenant_id, Customer.id.in_(customer_ids))
        ):
            customers[customer_id] = name
    return RetailDataset(
        today=today,
        products=products,
        orders=sorted(orders.values(), key=lambda order: order.placed_at),
        customers=customers,
    )


# ---------------------------------------------------------------------------
# Demand forecasting and inventory risk
# ---------------------------------------------------------------------------


def daily_units(dataset: RetailDataset, days: int = 56) -> dict[str, list[int]]:
    """Units sold per product for each of the last `days` complete days (oldest first)."""
    start = dataset.today - timedelta(days=days)
    series = {product_id: [0] * days for product_id in dataset.products}
    for order in dataset.orders:
        index = (order.day - start).days
        if 0 <= index < days:
            for product_id, quantity in order.items.items():
                if product_id in series:
                    series[product_id][index] += quantity
    return series


def local_forecast(history: list[int], start: date, horizon: int) -> tuple[list[float], float]:
    """Seasonal-level-trend forecast: weekday profile x winsorised level x damped trend."""
    days = len(history)
    first_day = start - timedelta(days=days)
    total_mean = sum(history) / days if days else 0.0
    if total_mean == 0:
        return [0.0] * horizon, 0.0
    weekday_totals: dict[int, list[int]] = defaultdict(list)
    for index, units in enumerate(history):
        weekday_totals[(first_day + timedelta(days=index)).weekday()].append(units)
    weekday_factor = {
        weekday: 0.5 + 0.5 * (statistics.fmean(values) / total_mean) for weekday, values in weekday_totals.items()
    }

    def deseasonalised(window: list[int], offset: int) -> float:
        cap = max(2 * statistics.median(history[-28:]), 1)
        values = [
            min(units, cap) / weekday_factor.get((first_day + timedelta(days=offset + index)).weekday(), 1.0)
            for index, units in enumerate(window)
        ]
        return statistics.fmean(values)

    recent = deseasonalised(history[-14:], days - 14)
    previous = deseasonalised(history[-28:-14], days - 28)
    ratio = min(1.6, max(0.7, recent / previous)) if previous > 0 else 1.0
    daily_growth = ratio ** (1 / 14)
    forecast = []
    for step in range(horizon):
        day = start + timedelta(days=step)
        growth = daily_growth ** min(step + 7, 35)
        forecast.append(recent * growth * weekday_factor.get(day.weekday(), 1.0))
    return forecast, ratio - 1


def inventory_forecast(
    dataset: RetailDataset,
    horizon_days: int = 14,
    external_forecasts: dict[str, list[float]] | None = None,
) -> list[dict[str, Any]]:
    history = daily_units(dataset)
    rows = []
    for product in dataset.products.values():
        window = product.lead_time_days + horizon_days
        forecast, trend = local_forecast(history[product.id], dataset.today, window)
        source = "local_seasonal_trend"
        external = (external_forecasts or {}).get(product.sku)
        if external:
            forecast = [max(0.0, value) for value in external[:window]]
            forecast += [forecast[-1]] * (window - len(forecast))
            source = "bigquery_ai_forecast"
        stock = product.on_hand
        stockout_day: int | None = None
        for step, demand in enumerate(forecast):
            if step == product.lead_time_days:
                stock += product.on_order
            stock -= demand
            if stock <= 0 and stockout_day is None:
                stockout_day = step + 1
        first_fortnight = forecast[:14]
        average_daily = sum(first_fortnight) / len(first_fortnight) if first_fortnight else 0.0
        demand_over_window = sum(forecast)
        need = demand_over_window + product.safety_stock - product.on_hand - product.on_order
        recommended = 0
        if need > 0:
            recommended = math.ceil(need / product.case_pack) * product.case_pack
        if stockout_day is not None and stockout_day <= product.lead_time_days:
            risk = "critical"
        elif stockout_day is not None and stockout_day <= window:
            risk = "high"
        elif recommended > 0:
            risk = "watch"
        else:
            risk = "ok"
        recent_units = sum(history[product.id][-28:])
        rows.append(
            {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
                "supplier": product.supplier,
                "on_hand": product.on_hand,
                "on_order": product.on_order,
                "safety_stock": product.safety_stock,
                "lead_time_days": product.lead_time_days,
                "case_pack": product.case_pack,
                "unit_cost_cents": product.cost_cents,
                "units_last_28_days": recent_units,
                "forecast_daily": [round(value, 2) for value in forecast[:horizon_days]],
                "forecast_units": round(sum(forecast[:horizon_days]), 1),
                "average_daily_forecast": round(average_daily, 2),
                "trend_pct": round(trend * 100, 1),
                "days_of_cover": round(product.on_hand / average_daily, 1) if average_daily > 0 else None,
                "stockout_date": (
                    (dataset.today + timedelta(days=stockout_day - 1)).isoformat() if stockout_day else None
                ),
                "risk": risk,
                "recommended_quantity": recommended,
                "recommended_cost_cents": recommended * product.cost_cents,
                "source": source,
            }
        )
    order = {"critical": 0, "high": 1, "watch": 2, "ok": 3}
    rows.sort(key=lambda row: (order[row["risk"]], row["days_of_cover"] if row["days_of_cover"] is not None else 1e9))
    return rows


async def forecast_with_backend(
    dataset: RetailDataset, settings: Settings, horizon_days: int = 14
) -> list[dict[str, Any]]:
    external = None
    if settings.retail_analytics_backend == "bigquery":
        from services.api.omni_api.retail_gcp import fetch_bigquery_forecast

        try:
            external = await asyncio.to_thread(fetch_bigquery_forecast, settings, dataset.today)
        except Exception:
            # BigQuery is optional at request time; the local model keeps the console usable.
            external = None
    return inventory_forecast(dataset, horizon_days, external)


# ---------------------------------------------------------------------------
# Customer segmentation (RFM)
# ---------------------------------------------------------------------------


def customer_rfm(dataset: RetailDataset) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for order in dataset.orders:
        if not order.customer_id:
            continue
        entry = stats.setdefault(
            order.customer_id,
            {"first": order.day, "last": order.day, "orders": 0, "monetary": 0, "categories": Counter()},
        )
        entry["first"] = min(entry["first"], order.day)
        entry["last"] = max(entry["last"], order.day)
        entry["orders"] += 1
        entry["monetary"] += order.total_cents
        for product_id, revenue in order.revenue_by_product.items():
            product = dataset.products.get(product_id)
            if product:
                entry["categories"][product.category] += revenue
    if not stats:
        return {}
    spend = sorted(entry["monetary"] for entry in stats.values())
    high_value = spend[min(len(spend) - 1, int(len(spend) * 0.7))]
    for entry in stats.values():
        recency = (dataset.today - entry["last"]).days
        frequency = entry["orders"]
        tenure = (dataset.today - entry["first"]).days
        if recency > 75 and entry["monetary"] >= high_value:
            segment = "Lapsed high-value"
        elif recency <= 30 and frequency >= 8 and entry["monetary"] >= high_value:
            segment = "Champions"
        elif recency <= 60 and frequency >= 5:
            segment = "Loyal"
        elif frequency == 1 and tenure <= 45:
            segment = "New"
        elif recency <= 45 and frequency >= 2:
            segment = "Promising"
        elif recency <= 120 and frequency >= 2:
            segment = "At risk"
        else:
            segment = "Hibernating"
        entry.update({"recency_days": recency, "frequency": frequency, "segment": segment})
    return stats


def segment_summary(dataset: RetailDataset) -> list[dict[str, Any]]:
    rfm = customer_rfm(dataset)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in rfm.values():
        grouped[entry["segment"]].append(entry)
    total_customers = max(1, len(rfm))
    rows = []
    for name, (description, action) in SEGMENT_GUIDE.items():
        members = grouped.get(name, [])
        categories: Counter = Counter()
        for member in members:
            categories.update(member["categories"])
        orders = sum(member["frequency"] for member in members)
        revenue = sum(member["monetary"] for member in members)
        rows.append(
            {
                "segment": name,
                "description": description,
                "recommended_action": action,
                "customers": len(members),
                "share": round(len(members) / total_customers, 3),
                "revenue_cents": revenue,
                "avg_order_value_cents": round(revenue / orders) if orders else 0,
                "avg_recency_days": round(statistics.fmean(m["recency_days"] for m in members), 1) if members else None,
                "avg_orders": round(orders / len(members), 2) if members else 0,
                "top_categories": [category for category, _ in categories.most_common(3)],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Basket analysis (association rules)
# ---------------------------------------------------------------------------


def basket_rules(dataset: RetailDataset, min_pair_count: int = 5) -> list[dict[str, Any]]:
    baskets = [set(order.items) for order in dataset.orders]
    total = len(baskets)
    if not total:
        return []
    single: Counter = Counter()
    pairs: Counter = Counter()
    for basket in baskets:
        single.update(basket)
        if len(basket) > 1:
            pairs.update(tuple(sorted(pair)) for pair in combinations(basket, 2))
    rules = []
    for (left, right), together in pairs.items():
        if together < min_pair_count or left not in dataset.products or right not in dataset.products:
            continue
        for anchor, companion in ((left, right), (right, left)):
            confidence = together / single[anchor]
            lift = confidence / (single[companion] / total)
            rules.append(
                {
                    "anchor_id": anchor,
                    "anchor_sku": dataset.products[anchor].sku,
                    "anchor_name": dataset.products[anchor].name,
                    "companion_id": companion,
                    "companion_sku": dataset.products[companion].sku,
                    "companion_name": dataset.products[companion].name,
                    "orders_together": together,
                    "support": round(together / total, 4),
                    "confidence": round(confidence, 3),
                    "lift": round(lift, 2),
                }
            )
    rules.sort(key=lambda rule: (-rule["lift"], -rule["orders_together"]))
    return rules


def top_pairs(dataset: RetailDataset, limit: int = 12) -> list[dict[str, Any]]:
    seen: set[frozenset[str]] = set()
    pairs = []
    for rule in basket_rules(dataset):
        key = frozenset((rule["anchor_id"], rule["companion_id"]))
        if key in seen or rule["lift"] < 1.5:
            continue
        seen.add(key)
        pairs.append(rule)
        if len(pairs) == limit:
            break
    return pairs


def companions(dataset: RetailDataset, product_id: str, limit: int = 4) -> list[dict[str, Any]]:
    rules = [rule for rule in basket_rules(dataset) if rule["anchor_id"] == product_id]
    rules.sort(key=lambda rule: (-rule["confidence"], -rule["lift"]))
    return [rule for rule in rules if rule["lift"] >= 2][:limit]


# ---------------------------------------------------------------------------
# KPIs and personalisation
# ---------------------------------------------------------------------------


def overview(dataset: RetailDataset) -> dict[str, Any]:
    today = dataset.today
    current = [order for order in dataset.orders if 1 <= (today - order.day).days <= 30]
    previous = [order for order in dataset.orders if 31 <= (today - order.day).days <= 60]
    revenue = sum(order.total_cents for order in current)
    previous_revenue = sum(order.total_cents for order in previous)
    online_revenue = sum(order.total_cents for order in current if order.channel == "online")
    quarter_counts = Counter(
        order.customer_id for order in dataset.orders if order.customer_id and (today - order.day).days <= 90
    )
    daily: dict[date, dict[str, int]] = {}
    for offset in range(60, 0, -1):
        daily[today - timedelta(days=offset)] = {"revenue_cents": 0, "orders": 0}
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"revenue_cents": 0, "units": 0})
    products: Counter = Counter()
    for order in dataset.orders:
        if order.day in daily:
            daily[order.day]["revenue_cents"] += order.total_cents
            daily[order.day]["orders"] += 1
        if 1 <= (today - order.day).days <= 30:
            for product_id, units in order.items.items():
                product = dataset.products.get(product_id)
                if product:
                    categories[product.category]["revenue_cents"] += order.revenue_by_product.get(product_id, 0)
                    categories[product.category]["units"] += units
                    products[product_id] += order.revenue_by_product.get(product_id, 0)
    return {
        "as_of": today.isoformat(),
        "currency": next(iter(dataset.products.values())).currency if dataset.products else "SGD",
        "revenue_30d_cents": revenue,
        "revenue_change_pct": (
            round((revenue - previous_revenue) / previous_revenue * 100, 1) if previous_revenue else None
        ),
        "orders_30d": len(current),
        "average_order_value_cents": round(revenue / len(current)) if current else 0,
        "active_customers_30d": len({order.customer_id for order in current if order.customer_id}),
        "repeat_rate_90d": (
            round(sum(1 for count in quarter_counts.values() if count > 1) / len(quarter_counts), 3)
            if quarter_counts
            else 0
        ),
        "online_share": round(online_revenue / revenue, 3) if revenue else 0,
        "daily": [{"date": day.isoformat(), **values} for day, values in daily.items()],
        "categories": sorted(
            ({"category": name, **values} for name, values in categories.items()),
            key=lambda row: -row["revenue_cents"],
        ),
        "top_products": [
            {"product_id": product_id, "name": dataset.products[product_id].name, "revenue_cents": value}
            for product_id, value in products.most_common(5)
        ],
    }


def customer_profile(dataset: RetailDataset, customer_id: str) -> dict[str, Any]:
    rfm = customer_rfm(dataset).get(customer_id)
    orders = [order for order in dataset.orders if order.customer_id == customer_id]
    bought: Counter = Counter()
    for order in orders:
        bought.update(order.items)
    profile: dict[str, Any] = {
        "customer_id": customer_id,
        "name": dataset.customers.get(customer_id),
        "segment": rfm["segment"] if rfm else "New",
        "orders": rfm["frequency"] if rfm else 0,
        "lifetime_value_cents": rfm["monetary"] if rfm else 0,
        "recency_days": rfm["recency_days"] if rfm else None,
        "favorite_categories": [category for category, _ in rfm["categories"].most_common(3)] if rfm else [],
        "top_products": [
            dataset.products[product_id].name
            for product_id, _ in bought.most_common(5)
            if product_id in dataset.products
        ],
        "recent_orders": [
            {
                "number": order.number,
                "placed_at": order.placed_at.isoformat(),
                "channel": order.channel,
                "total_cents": order.total_cents,
                "items": [
                    dataset.products[product_id].name for product_id in order.items if product_id in dataset.products
                ],
            }
            for order in reversed(orders[-5:])
        ],
    }
    if rfm:
        profile["segment_action"] = SEGMENT_GUIDE[rfm["segment"]][1]
    return profile


def personalised_picks(dataset: RetailDataset, customer_id: str | None, limit: int = 6) -> list[dict[str, Any]]:
    history = daily_units(dataset, days=14)
    velocity = {product_id: sum(units) for product_id, units in history.items()}
    if not customer_id:
        ranked = sorted(dataset.products.values(), key=lambda product: -velocity.get(product.id, 0))
        return [
            {**product.card(), "reason": "Trending at Harbourline this fortnight"}
            for product in ranked
            if product.on_hand > 0
        ][:limit]
    profile = customer_profile(dataset, customer_id)
    orders = [order for order in dataset.orders if order.customer_id == customer_id]
    recently_bought = {
        product_id for order in orders if (dataset.today - order.day).days <= 45 for product_id in order.items
    }
    ever_bought = {product_id for order in orders for product_id in order.items}
    scores: dict[str, tuple[float, str]] = {}
    for anchor in list(recently_bought)[:6]:
        for rule in companions(dataset, anchor):
            if rule["companion_id"] not in ever_bought:
                score = 3 + rule["lift"]
                if score > scores.get(rule["companion_id"], (0, ""))[0]:
                    scores[rule["companion_id"]] = (score, f"Often bought with your {rule['anchor_name']}")
    favorites = profile["favorite_categories"]
    for product in dataset.products.values():
        if product.category in favorites and product.id not in recently_bought:
            rank = favorites.index(product.category)
            score = 2 - rank * 0.5 + velocity.get(product.id, 0) / 100
            if score > scores.get(product.id, (0, ""))[0]:
                reason = f"Popular in {product.category}, one of your favourite categories"
                if product.id in ever_bought:
                    reason = f"Time to restock? You bought this before in {product.category}"
                scores[product.id] = (score, reason)
    ranked = sorted(scores.items(), key=lambda item: -item[1][0])
    picks = [
        {**dataset.products[product_id].card(), "reason": reason}
        for product_id, (_, reason) in ranked
        if dataset.products[product_id].on_hand > 0
    ]
    return picks[:limit] or personalised_picks(dataset, None, limit)
