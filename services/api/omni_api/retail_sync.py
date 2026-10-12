"""Publish the retail tenant to Vertex AI Search and BigQuery.

Runs as a Cloud Run job (`python -m services.api.omni_api.retail_sync --all`)
after seeding and on a schedule, so request handlers only ever read.
"""

import argparse
import asyncio
from datetime import timezone

from sqlalchemy import select

from services.api.omni_api.config import get_settings
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import InventoryLevel, Product, SalesOrder, SalesOrderLine, Tenant
from services.api.omni_api.retail_analytics import load_dataset, local_day
from services.api.omni_api.retail_gcp import sync_bigquery, sync_vertex_search_catalog
from services.api.omni_api.retail_seed import RETAIL_TENANT_SLUG


async def export_tables(tenant_slug: str) -> tuple[list[dict], dict[str, list[dict]]]:
    engine = build_engine(get_settings().database_url)
    try:
        async with build_session_factory(engine)() as session:
            tenant = await session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
            if tenant is None:
                raise SystemExit(f"Tenant {tenant_slug!r} not found; run the retail seed first")
            dataset = await load_dataset(session, tenant.id)
            catalog = [{**product.card(), "on_hand": product.on_hand} for product in dataset.products.values()]
            sku_by_id = {product.id: product.sku for product in dataset.products.values()}

            products = [
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "brand": product.brand,
                    "category": product.category,
                    "price": product.price_cents / 100,
                    "unit_cost": product.cost_cents / 100,
                }
                for product in await session.scalars(select(Product).where(Product.tenant_id == tenant.id))
            ]
            inventory = [
                {
                    "sku": sku_by_id[level.product_id],
                    "on_hand": level.on_hand,
                    "on_order": dataset.products[level.product_id].on_order,
                    "safety_stock": level.safety_stock,
                    "lead_time_days": level.lead_time_days,
                    "case_pack": level.case_pack,
                    "supplier": level.supplier,
                }
                for level in await session.scalars(select(InventoryLevel).where(InventoryLevel.tenant_id == tenant.id))
                if level.product_id in sku_by_id
            ]
            orders = []
            order_dates = {}
            for order in await session.scalars(select(SalesOrder).where(SalesOrder.tenant_id == tenant.id)):
                placed = order.placed_at if order.placed_at.tzinfo else order.placed_at.replace(tzinfo=timezone.utc)
                order_dates[order.id] = local_day(placed).isoformat()
                orders.append(
                    {
                        "order_id": order.id,
                        "number": order.number,
                        "customer_id": order.customer_id,
                        "placed_at": placed.isoformat(),
                        "order_date": order_dates[order.id],
                        "channel": order.channel,
                        "store": order.store,
                        "subtotal": order.subtotal_cents / 100,
                        "discount": order.discount_cents / 100,
                        "total": order.total_cents / 100,
                    }
                )
            lines = [
                {
                    "order_id": line.order_id,
                    "order_date": order_dates[line.order_id],
                    "sku": sku_by_id[line.product_id],
                    "quantity": line.quantity,
                    "unit_price": line.unit_price_cents / 100,
                    "line_total": line.line_total_cents / 100,
                }
                for line in await session.scalars(select(SalesOrderLine).where(SalesOrderLine.tenant_id == tenant.id))
                if line.order_id in order_dates and line.product_id in sku_by_id
            ]
            return catalog, {"products": products, "inventory": inventory, "orders": orders, "order_lines": lines}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync retail data to Vertex AI Search and BigQuery")
    parser.add_argument("--search", action="store_true", help="reconcile the Vertex AI Search catalog")
    parser.add_argument("--bigquery", action="store_true", help="load BigQuery and rebuild AI forecasts")
    parser.add_argument("--all", action="store_true", help="run every sync")
    parser.add_argument("--tenant-slug", default=RETAIL_TENANT_SLUG)
    args = parser.parse_args()
    if not (args.search or args.bigquery or args.all):
        parser.error("choose --search, --bigquery, or --all")
    settings = get_settings()
    catalog, tables = asyncio.run(export_tables(args.tenant_slug))
    if args.search or args.all:
        print("Vertex AI Search:", sync_vertex_search_catalog(settings, catalog))
    if args.bigquery or args.all:
        for step in sync_bigquery(settings, tables):
            print("BigQuery:", step)


if __name__ == "__main__":
    main()
