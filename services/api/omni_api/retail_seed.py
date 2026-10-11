"""Generate a realistic, reproducible Harbourline sales history.

The generator models weekly traffic, paydays, Singapore mega-sale days (9.9,
10.10), the monsoon ramp, Deepavali/Christmas gifting, customer personas with
category preferences and churn, and product affinities. Inventory is then set
from recent velocity so a handful of seasonal SKUs are genuinely at risk.
"""

import argparse
import asyncio
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.api.omni_api.config import get_settings
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import (
    Campaign,
    Customer,
    InventoryLevel,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
    Tenant,
    TenantMembership,
    User,
    new_id,
)
from services.api.omni_api.retail_catalog import AFFINITIES, CATALOG, FIRST_NAMES, LAST_NAMES, STORES, CatalogItem
from services.api.omni_api.seed import DEV_USER_EMAIL, seed_development_data

SINGAPORE = ZoneInfo("Asia/Singapore")
RETAIL_TENANT_SLUG = "harbourline"
RETAIL_TENANT_NAME = "Harbourline Retail"

DEEPAVALI = {2025: date(2025, 10, 20), 2026: date(2026, 11, 8), 2027: date(2027, 10, 29)}
GIFTING_DAYS = ((2, 14), (5, 10), (9, 25), (12, 25))
WEEKDAY_TRAFFIC = (0.9, 0.88, 0.92, 0.97, 1.1, 1.3, 1.22)


def _days_until(day: date, target: date) -> int:
    return (target - day).days


def season_factor(item: CatalogItem, day: date) -> float:
    if item.season == "rain":
        if day.month in (9, 10, 11, 12):
            ramp = min(1.0, max(0.0, (day - date(day.year, 9, 1)).days / 80))
            return 1.0 + 1.8 * ramp**1.5
        if day.month == 1:
            return 2.2
        if day.month in (4, 5):
            return 1.3
        return 0.85
    if item.season == "deepavali":
        festival = DEEPAVALI.get(day.year, date(day.year, 11, 1))
        until = _days_until(day, festival)
        if 0 <= until <= 45:
            return 1.0 + 4.0 * (1 - until / 45) ** 2
        return 0.35
    if item.season == "christmas":
        until = _days_until(day, date(day.year, 12, 25))
        return 1.0 + 3.0 * (1 - until / 50) ** 2 if 0 <= until <= 50 else 0.5
    if item.season == "gift":
        festivals = [date(day.year, month, dom) for month, dom in GIFTING_DAYS]
        festivals.append(DEEPAVALI.get(day.year, date(day.year, 11, 1)))
        boost = max(
            (math.exp(-((_days_until(day, f) / 10) ** 2)) for f in festivals if 0 <= _days_until(day, f) <= 21),
            default=0.0,
        )
        return 1.0 + 0.9 * boost
    if item.season == "holiday":
        if day.month == 6 or (day.month == 12 and day.day >= 1):
            return 1.6
        if day.month == 9 and 5 <= day.day <= 13:
            return 1.35
        return 1.0
    return 1.0


def is_mega_sale(day: date) -> bool:
    return day.month == day.day and day.month >= 3


def traffic_factor(day: date, progress: float) -> float:
    factor = WEEKDAY_TRAFFIC[day.weekday()] * (1 + 0.18 * progress)
    if 25 <= day.day <= 28:
        factor *= 1.12
    if is_mega_sale(day):
        factor *= 1.9
    return factor


def poisson(rng: random.Random, lam: float) -> int:
    threshold, count, product = math.exp(-lam), 0, 1.0
    while True:
        product *= rng.random()
        if product <= threshold:
            return count
        count += 1


@dataclass
class Persona:
    customer_id: str
    kind: str
    monthly_orders: float
    preferred: tuple[str, ...]
    starts: date
    ends: date
    basket_bias: float


def build_personas(rng: random.Random, count: int, start: date, today: date) -> tuple[list[dict], list[Persona]]:
    categories = sorted({item.category for item in CATALOG})
    customers: list[dict] = []
    personas: list[Persona] = []
    for index in range(count):
        roll = rng.random()
        if roll < 0.06:
            kind, monthly, bias = "vip", rng.uniform(2.0, 4.0), 1.4
        elif roll < 0.36:
            kind, monthly, bias = "regular", rng.uniform(0.6, 1.5), 1.0
        elif roll < 0.76:
            kind, monthly, bias = "occasional", rng.uniform(0.15, 0.5), 0.9
        elif roll < 0.90:
            kind, monthly, bias = "churned", rng.uniform(1.5, 3.0), 1.2
        else:
            kind, monthly, bias = "new", rng.uniform(0.8, 2.0), 1.0
        starts = today - timedelta(days=rng.randint(5, 35)) if kind == "new" else start
        ends = today - timedelta(days=rng.randint(80, 150)) if kind == "churned" else today
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        customer_id = new_id()
        handle = f"{first}.{last}".lower().replace(" ", "").replace("'", "")
        customers.append(
            {
                "id": customer_id,
                "name": f"{first} {last}",
                "email": f"{handle}{index + 1}@example.com",
                "external_ref": f"HL-C{index + 1:05d}",
                "notes": "Harbourline loyalty member",
            }
        )
        preferred = tuple(rng.sample(categories, k=1 if rng.random() < 0.55 else 2))
        personas.append(Persona(customer_id, kind, monthly, preferred, starts, ends, bias))
    return customers, personas


def choose_item(rng: random.Random, items: list[CatalogItem], day: date) -> CatalogItem:
    weights = [item.demand * season_factor(item, day) for item in items]
    return rng.choices(items, weights=weights, k=1)[0]


def build_basket(
    rng: random.Random, day: date, persona: Persona | None, by_category: dict[str, list[CatalogItem]]
) -> dict[str, int]:
    every_item = [item for group in by_category.values() for item in group]
    if persona and rng.random() < 0.72:
        pool = [item for category in persona.preferred for item in by_category[category]]
        anchor = choose_item(rng, pool, day)
    else:
        anchor = choose_item(rng, every_item, day)
    basket = {anchor.sku: 1}
    for left, right, probability in AFFINITIES:
        if left == anchor.sku and rng.random() < probability:
            basket[right] = 1
    extra_probability = 0.42 * (persona.basket_bias if persona else 0.8)
    while rng.random() < extra_probability and len(basket) < 5:
        basket.setdefault(choose_item(rng, every_item, day).sku, 1)
        extra_probability *= 0.5
    return basket


async def ensure_retail_tenant(session: AsyncSession) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == RETAIL_TENANT_SLUG))
    if tenant is None:
        tenant = Tenant(name=RETAIL_TENANT_NAME, slug=RETAIL_TENANT_SLUG)
        session.add(tenant)
        await session.flush()
    owner = await session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if owner is not None:
        membership = await session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id, TenantMembership.user_id == owner.id
            )
        )
        if membership is None:
            session.add(TenantMembership(tenant_id=tenant.id, user_id=owner.id, role="owner"))
    return tenant


async def clear_retail_data(session: AsyncSession, tenant_id: str) -> None:
    for model in (Campaign, PurchaseOrderLine, PurchaseOrder, SalesOrderLine, SalesOrder, InventoryLevel, Product):
        await session.execute(delete(model).where(model.tenant_id == tenant_id))
    await session.execute(delete(Customer).where(Customer.tenant_id == tenant_id, Customer.external_ref.like("HL-C%")))


async def seed_retail_data(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    days: int = 180,
    scale: float = 1.0,
    today: date | None = None,
    rng_seed: int = 20261011,
    reset: bool = False,
) -> dict[str, int | str]:
    """Create the Harbourline tenant, catalog, customers, orders, and inventory."""
    await seed_development_data(session_factory)
    today = today or datetime.now(SINGAPORE).date()
    rng = random.Random(rng_seed)
    async with session_factory() as session:
        tenant = await ensure_retail_tenant(session)
        existing = await session.scalar(select(Product.id).where(Product.tenant_id == tenant.id).limit(1))
        if existing and not reset:
            await session.commit()
            return {"tenant_id": tenant.id, "status": "already_seeded"}
        if existing:
            await clear_retail_data(session, tenant.id)

        products = {item.sku: new_id() for item in CATALOG}
        await session.execute(
            insert(Product),
            [
                {
                    "id": products[item.sku],
                    "tenant_id": tenant.id,
                    "sku": item.sku,
                    "name": item.name,
                    "brand": item.brand,
                    "category": item.category,
                    "description": item.description,
                    "price_cents": round(item.price * 100),
                    "cost_cents": round(item.price * 100 * (1 - item.margin)),
                    "currency": "SGD",
                    "tags": list(item.tags),
                    "attributes": dict(item.attributes),
                    "image_url": None,
                    "is_active": True,
                }
                for item in CATALOG
            ],
        )

        start = today - timedelta(days=days)
        customers, personas = build_personas(rng, max(20, int(1200 * scale)), start, today)
        await session.execute(insert(Customer), [{**row, "tenant_id": tenant.id, "version": 1} for row in customers])

        by_category: dict[str, list[CatalogItem]] = {}
        for item in CATALOG:
            by_category.setdefault(item.category, []).append(item)
        catalog = {item.sku: item for item in CATALOG}

        orders: list[dict] = []
        lines: list[dict] = []
        units_by_day: dict[tuple[str, date], int] = {}
        sequence = 0
        for offset in range(days):
            day = start + timedelta(days=offset)
            traffic = traffic_factor(day, offset / max(1, days - 1))
            shoppers: list[Persona | None] = [None] * poisson(rng, 15 * scale * traffic)
            for persona in personas:
                if persona.starts <= day <= persona.ends and rng.random() < persona.monthly_orders / 30 * traffic:
                    shoppers.append(persona)
            for persona in shoppers:
                sequence += 1
                basket = build_basket(rng, day, persona, by_category)
                online_share = 0.85 if is_mega_sale(day) else 0.58
                channel = "online" if rng.random() < online_share else "store"
                placed = datetime.combine(day, time(rng.randint(8, 22), rng.randint(0, 59)), SINGAPORE)
                order_id = new_id()
                subtotal = 0
                for sku, _ in basket.items():
                    item = catalog[sku]
                    quantity = rng.choice((1, 1, 2, 3)) if item.multi_quantity else 1
                    price = round(item.price * 100)
                    subtotal += price * quantity
                    lines.append(
                        {
                            "id": new_id(),
                            "tenant_id": tenant.id,
                            "order_id": order_id,
                            "product_id": products[sku],
                            "quantity": quantity,
                            "unit_price_cents": price,
                            "line_total_cents": price * quantity,
                        }
                    )
                    units_by_day[(sku, day)] = units_by_day.get((sku, day), 0) + quantity
                discount_rate = 0.15 if is_mega_sale(day) and channel == "online" else 0.0
                if persona and persona.kind == "vip":
                    discount_rate = max(discount_rate, 0.05)
                discount = round(subtotal * discount_rate)
                orders.append(
                    {
                        "id": order_id,
                        "tenant_id": tenant.id,
                        "customer_id": persona.customer_id if persona else None,
                        "number": f"HL{day:%y%m%d}-{sequence:06d}",
                        "channel": channel,
                        "store": "Online" if channel == "online" else rng.choice(STORES),
                        "status": "completed",
                        "placed_at": placed.astimezone(timezone.utc),
                        "subtotal_cents": subtotal,
                        "discount_cents": discount,
                        "total_cents": subtotal - discount,
                        "currency": "SGD",
                    }
                )
        for batch_start in range(0, len(orders), 2000):
            await session.execute(insert(SalesOrder), orders[batch_start : batch_start + 2000])
        for batch_start in range(0, len(lines), 4000):
            await session.execute(insert(SalesOrderLine), lines[batch_start : batch_start + 4000])

        recent_days = [today - timedelta(days=offset) for offset in range(1, 15)]
        inventory = []
        for item in CATALOG:
            velocity = sum(units_by_day.get((item.sku, day), 0) for day in recent_days) / len(recent_days)
            velocity = max(velocity, item.demand * 0.3 * scale)
            # Healthy SKUs hold their cover on top of the replenishment lead time;
            # the deliberately tight seasonal SKUs drive the restock demo.
            cover = item.cover_days + item.lead_time_days if item.cover_days >= 15 else item.cover_days
            on_hand = max(item.case_pack, round(velocity * cover * rng.uniform(0.9, 1.1)))
            inventory.append(
                {
                    "tenant_id": tenant.id,
                    "product_id": products[item.sku],
                    "on_hand": on_hand,
                    "safety_stock": max(1, math.ceil(velocity * 3)),
                    "lead_time_days": item.lead_time_days,
                    "case_pack": item.case_pack,
                    "supplier": item.supplier,
                }
            )
        await session.execute(insert(InventoryLevel), inventory)
        await session.commit()
        return {
            "tenant_id": tenant.id,
            "status": "seeded",
            "products": len(CATALOG),
            "customers": len(customers),
            "orders": len(orders),
            "order_lines": len(lines),
        }


async def run_seed(reset: bool, days: int, scale: float) -> None:
    engine = build_engine(get_settings().database_url)
    try:
        summary = await seed_retail_data(build_session_factory(engine), days=days, scale=scale, reset=reset)
        print("Harbourline retail seed:", summary)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Harbourline retail demo tenant")
    parser.add_argument("--reset", action="store_true", help="replace existing retail demo data")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    asyncio.run(run_seed(args.reset, args.days, args.scale))


if __name__ == "__main__":
    main()
