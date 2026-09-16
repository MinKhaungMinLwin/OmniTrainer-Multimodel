"""Finish the operations MVP domain model."""

import sqlalchemy as sa
from alembic import op

revision = "0003_finish_operations_mvp"
down_revision = "0002_operations_vertical_slice"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("customers", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("invoices", sa.Column("paid_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("invoices", sa.Column("payment_status", sa.String(30), nullable=False, server_default="unpaid"))

    op.create_table(
        "customer_contacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(50)),
        sa.Column("role", sa.String(100)),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_customer_contacts_tenant_id", "customer_contacts", ["tenant_id"])
    op.create_index("ix_customer_contacts_customer_id", "customer_contacts", ["customer_id"])
    op.create_table(
        "customer_locations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("address_line1", sa.String(300), nullable=False),
        sa.Column("address_line2", sa.String(300)),
        sa.Column("city", sa.String(150), nullable=False),
        sa.Column("region", sa.String(150)),
        sa.Column("postal_code", sa.String(30)),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_customer_locations_tenant_id", "customer_locations", ["tenant_id"])
    op.create_index("ix_customer_locations_customer_id", "customer_locations", ["customer_id"])
    op.add_column(
        "jobs",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("customer_locations.id", ondelete="SET NULL")),
    )
    op.create_index("ix_jobs_location_id", "jobs", ["location_id"])
    op.create_table(
        "technicians",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(50)),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "email", name="uq_technicians_tenant_id"),
    )
    op.create_index("ix_technicians_tenant_id", "technicians", ["tenant_id"])
    op.add_column(
        "appointments",
        sa.Column("technician_id", sa.String(36), sa.ForeignKey("technicians.id", ondelete="SET NULL")),
    )
    op.add_column("appointments", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_appointments_technician_id", "appointments", ["technician_id"])
    op.create_table(
        "availability_windows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("technician_id", sa.String(36), sa.ForeignKey("technicians.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_availability_windows_tenant_id", "availability_windows", ["tenant_id"])
    op.create_index("ix_availability_windows_technician_id", "availability_windows", ["technician_id"])
    op.create_table(
        "job_status_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(30)),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_job_status_history_tenant_id", "job_status_history", ["tenant_id"])
    op.create_index("ix_job_status_history_job_id", "job_status_history", ["job_id"])
    op.create_table(
        "job_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("body", sa.Text(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_job_notes_tenant_id", "job_notes", ["tenant_id"])
    op.create_index("ix_job_notes_job_id", "job_notes", ["job_id"])
    op.create_table(
        "job_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False, unique=True),
        *timestamps(),
    )
    op.create_index("ix_job_attachments_tenant_id", "job_attachments", ["tenant_id"])
    op.create_index("ix_job_attachments_job_id", "job_attachments", ["job_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(50), nullable=False),
        sa.Column("external_ref", sa.String(150)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "external_ref", name="uq_payments_tenant_id"),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("job_attachments")
    op.drop_table("job_notes")
    op.drop_table("job_status_history")
    op.drop_table("availability_windows")
    op.drop_index("ix_appointments_technician_id", table_name="appointments")
    op.drop_column("appointments", "version")
    op.drop_column("appointments", "technician_id")
    op.drop_table("technicians")
    op.drop_index("ix_jobs_location_id", table_name="jobs")
    op.drop_column("jobs", "location_id")
    op.drop_table("customer_locations")
    op.drop_table("customer_contacts")
    op.drop_column("invoices", "payment_status")
    op.drop_column("invoices", "paid_cents")
    op.drop_column("customers", "version")
