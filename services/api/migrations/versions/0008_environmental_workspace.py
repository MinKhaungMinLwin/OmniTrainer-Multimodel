"""Add tenant-scoped environmental projects, workbooks, and reviewed reports."""

import sqlalchemy as sa
from alembic import op

revision = "0008_environmental_workspace"
down_revision = "0007_call_intelligence"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "environmental_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("jurisdiction", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_environmental_projects_tenant_code"),
    )
    op.create_index("ix_environmental_projects_tenant_id", "environmental_projects", ["tenant_id"])

    op.create_table(
        "environmental_workbooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("environmental_projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("uploaded_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False, unique=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_environmental_workbooks_tenant_id", "environmental_workbooks", ["tenant_id"])
    op.create_index("ix_environmental_workbooks_project_id", "environmental_workbooks", ["project_id"])
    op.create_index(
        "ix_environmental_workbooks_project_created", "environmental_workbooks", ["project_id", "created_at"]
    )

    op.create_table(
        "environmental_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("environmental_projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workbook_id",
            sa.String(36),
            sa.ForeignKey("environmental_workbooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("review_reason", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_environmental_reports_tenant_id", "environmental_reports", ["tenant_id"])
    op.create_index("ix_environmental_reports_project_id", "environmental_reports", ["project_id"])
    op.create_index("ix_environmental_reports_workbook_id", "environmental_reports", ["workbook_id"])
    op.create_index("ix_environmental_reports_project_created", "environmental_reports", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("environmental_reports")
    op.drop_table("environmental_workbooks")
    op.drop_table("environmental_projects")
