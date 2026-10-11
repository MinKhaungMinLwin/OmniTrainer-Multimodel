"""Add retail catalog, inventory, sales orders, purchase orders, and campaigns."""

import sqlalchemy as sa
from alembic import op

revision = "0009_retail_commerce"
down_revision = "0008_environmental_workspace"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def tenant_column() -> sa.Column:
    return sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("brand", sa.String(120), nullable=False),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("image_url", sa.String(500)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index("ix_products_tenant_category", "products", ["tenant_id", "category"])

    op.create_table(
        "inventory_levels",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("safety_stock", sa.Integer(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("case_pack", sa.Integer(), nullable=False),
        sa.Column("supplier", sa.String(200), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "product_id", name="uq_inventory_levels_tenant_product"),
    )
    op.create_index("ix_inventory_levels_tenant_id", "inventory_levels", ["tenant_id"])
    op.create_index("ix_inventory_levels_product_id", "inventory_levels", ["product_id"])

    op.create_table(
        "sales_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("store", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "number", name="uq_sales_orders_tenant_number"),
    )
    op.create_index("ix_sales_orders_tenant_id", "sales_orders", ["tenant_id"])
    op.create_index("ix_sales_orders_customer_id", "sales_orders", ["customer_id"])
    op.create_index("ix_sales_orders_tenant_placed", "sales_orders", ["tenant_id", "placed_at"])

    op.create_table(
        "sales_order_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
    )
    op.create_index("ix_sales_order_lines_tenant_id", "sales_order_lines", ["tenant_id"])
    op.create_index("ix_sales_order_lines_order_id", "sales_order_lines", ["order_id"])
    op.create_index("ix_sales_order_lines_product_id", "sales_order_lines", ["product_id"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("total_cost_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("forecast", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "number", name="uq_purchase_orders_tenant_number"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column(
            "purchase_order_id",
            sa.String(36),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost_cents", sa.Integer(), nullable=False),
    )
    op.create_index("ix_purchase_order_lines_tenant_id", "purchase_order_lines", ["tenant_id"])
    op.create_index("ix_purchase_order_lines_purchase_order_id", "purchase_order_lines", ["purchase_order_id"])
    op.create_index("ix_purchase_order_lines_product_id", "purchase_order_lines", ["product_id"])

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_column(),
        sa.Column("segment", sa.String(60), nullable=False),
        sa.Column("offer", sa.String(300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("audience_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_campaigns_tenant_id", "campaigns", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("campaigns")
    op.drop_table("purchase_order_lines")
    op.drop_table("purchase_orders")
    op.drop_table("sales_order_lines")
    op.drop_table("sales_orders")
    op.drop_table("inventory_levels")
    op.drop_table("products")
