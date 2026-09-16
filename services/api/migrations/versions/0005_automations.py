"""Add versioned durable business automations and run history."""

import sqlalchemy as sa
from alembic import op

revision = "0005_automations"
down_revision = "0004_ai_copilot"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def tenant_id() -> sa.Column:
    return sa.Column(
        "tenant_id",
        sa.String(36),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "automation_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("template_key", sa.String(100)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("kill_reason", sa.Text()),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "name", name="uq_automation_definitions_tenant_id"),
    )
    op.create_index("ix_automation_definitions_tenant_id", "automation_definitions", ["tenant_id"])
    op.create_index("ix_automation_definitions_tenant_status", "automation_definitions", ["tenant_id", "status"])
    op.create_table(
        "automation_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey("automation_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(80), nullable=False),
        sa.Column("trigger_config", sa.JSON(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("approval_rule", sa.JSON(), nullable=False),
        sa.Column("rate_limit_per_hour", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        *timestamps(),
        sa.UniqueConstraint("definition_id", "version", name="uq_automation_versions_definition_id"),
    )
    op.create_index("ix_automation_versions_tenant_id", "automation_versions", ["tenant_id"])
    op.create_index("ix_automation_versions_definition_id", "automation_versions", ["definition_id"])
    op.create_table(
        "automation_trigger_events",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("trigger_type", sa.String(80), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_automation_trigger_events_tenant_id"),
    )
    op.create_index("ix_automation_trigger_events_tenant_id", "automation_trigger_events", ["tenant_id"])
    op.create_index("ix_automation_trigger_events_trigger_type", "automation_trigger_events", ["trigger_type"])
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey("automation_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("automation_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trigger_event_id",
            sa.String(36),
            sa.ForeignKey("automation_trigger_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("replay_of_run_id", sa.String(36), sa.ForeignKey("automation_runs.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("changed_resources", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("definition_id", "trigger_event_id", name="uq_automation_runs_definition_id"),
    )
    for column in ("tenant_id", "definition_id", "version_id", "trigger_event_id"):
        op.create_index(f"ix_automation_runs_{column}", "automation_runs", [column])
    op.create_index("ix_automation_runs_tenant_status", "automation_runs", ["tenant_id", "status"])
    op.create_table(
        "automation_run_events",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_automation_run_events_run_id"),
    )
    op.create_index("ix_automation_run_events_tenant_id", "automation_run_events", ["tenant_id"])
    op.create_index("ix_automation_run_events_run_id", "automation_run_events", ["run_id"])
    op.create_index("ix_automation_run_events_run_sequence", "automation_run_events", ["run_id", "sequence"])
    op.create_table(
        "automation_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("automation_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("proposed_actions", sa.JSON(), nullable=False),
        sa.Column("decided_actions", sa.JSON()),
        sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_automation_approvals_tenant_id", "automation_approvals", ["tenant_id"])
    op.create_index("ix_automation_approvals_run_id", "automation_approvals", ["run_id"])
    op.create_table(
        "automation_dead_letters",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("automation_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_automation_dead_letters_tenant_id", "automation_dead_letters", ["tenant_id"])
    op.create_index("ix_automation_dead_letters_run_id", "automation_dead_letters", ["run_id"])


def downgrade() -> None:
    op.drop_table("automation_dead_letters")
    op.drop_table("automation_approvals")
    op.drop_table("automation_run_events")
    op.drop_table("automation_runs")
    op.drop_table("automation_trigger_events")
    op.drop_table("automation_versions")
    op.drop_table("automation_definitions")
