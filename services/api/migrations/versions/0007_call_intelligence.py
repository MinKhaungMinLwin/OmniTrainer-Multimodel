"""Add replayable call-data and conversation-intelligence layers."""

import sqlalchemy as sa
from alembic import op

revision = "0007_call_intelligence"
down_revision = "0006_voice_pilot"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def tenant_id() -> sa.Column:
    return sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "call_raw_payloads",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="SET NULL")),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_call_id", sa.String(200), nullable=False),
        sa.Column("provider_event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False, unique=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_call_raw_payloads_provider_event"),
    )
    op.create_index("ix_call_raw_payloads_tenant_id", "call_raw_payloads", ["tenant_id"])
    op.create_index("ix_call_raw_payloads_provider_call_id", "call_raw_payloads", ["provider_call_id"])
    op.create_index("ix_call_raw_payloads_tenant_received", "call_raw_payloads", ["tenant_id", "received_at"])

    op.create_table(
        "call_transcript_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "source_revision_id",
            sa.String(36),
            sa.ForeignKey("call_transcript_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("redacted_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("processor_version", sa.String(50), nullable=False),
        sa.Column("corrected_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("correction_reason", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("call_id", "version", name="uq_call_transcript_revisions_call_version"),
    )
    op.create_index("ix_call_transcript_revisions_tenant_id", "call_transcript_revisions", ["tenant_id"])
    op.create_index("ix_call_transcript_revisions_call_id", "call_transcript_revisions", ["call_id"])
    op.create_index("ix_call_transcript_revisions_tenant_status", "call_transcript_revisions", ["tenant_id", "status"])

    op.create_table(
        "call_intelligence",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "transcript_revision_id",
            sa.String(36),
            sa.ForeignKey("call_transcript_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(50), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("intent", sa.String(100), nullable=False),
        sa.Column("sentiment_trajectory", sa.JSON(), nullable=False),
        sa.Column("objections", sa.JSON(), nullable=False),
        sa.Column("compliance_flags", sa.JSON(), nullable=False),
        sa.Column("action_items", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(100), nullable=False),
        sa.Column("structured_fields", sa.JSON(), nullable=False),
        sa.Column("confidence_bps", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("corrected_fields", sa.JSON()),
        sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("review_reason", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("call_id", "version", name="uq_call_intelligence_call_version"),
    )
    op.create_index("ix_call_intelligence_tenant_id", "call_intelligence", ["tenant_id"])
    op.create_index("ix_call_intelligence_call_id", "call_intelligence", ["call_id"])
    op.create_index("ix_call_intelligence_transcript_revision_id", "call_intelligence", ["transcript_revision_id"])
    op.create_index("ix_call_intelligence_tenant_status", "call_intelligence", ["tenant_id", "status"])
    op.create_index("ix_call_intelligence_tenant_topic", "call_intelligence", ["tenant_id", "topic"])

    op.create_table(
        "call_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), unique=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("flow_key", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("answered", sa.Boolean(), nullable=False),
        sa.Column("contained", sa.Boolean(), nullable=False),
        sa.Column("transferred", sa.Boolean(), nullable=False),
        sa.Column("abandoned", sa.Boolean(), nullable=False),
        sa.Column("consented", sa.Boolean(), nullable=False),
        sa.Column("interruption_count", sa.Integer(), nullable=False),
        sa.Column("silence_count", sa.Integer(), nullable=False),
        sa.Column("tool_count", sa.Integer(), nullable=False),
        sa.Column("first_audio_ms", sa.Integer(), nullable=False),
        sa.Column("transcript_segments", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(100), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_call_facts_tenant_id", "call_facts", ["tenant_id"])
    op.create_index("ix_call_facts_call_id", "call_facts", ["call_id"])
    op.create_index("ix_call_facts_tenant_started", "call_facts", ["tenant_id", "started_at"])

    op.create_table(
        "call_pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("checkpoints", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("call_id", "pipeline_version", name="uq_call_pipeline_runs_call_version"),
    )
    op.create_index("ix_call_pipeline_runs_tenant_id", "call_pipeline_runs", ["tenant_id"])
    op.create_index("ix_call_pipeline_runs_call_id", "call_pipeline_runs", ["call_id"])
    op.create_index("ix_call_pipeline_runs_tenant_status", "call_pipeline_runs", ["tenant_id", "status"])

    op.create_table(
        "call_reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_snapshot_id", sa.String(200), nullable=False),
        sa.Column("provider_status", sa.String(50), nullable=False),
        sa.Column("provider_duration_ms", sa.Integer(), nullable=False),
        sa.Column("internal_status", sa.String(50), nullable=False),
        sa.Column("internal_duration_ms", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("discrepancies", sa.JSON(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("call_id", "provider_snapshot_id", name="uq_call_reconciliations_call_snapshot"),
    )
    op.create_index("ix_call_reconciliations_tenant_id", "call_reconciliations", ["tenant_id"])
    op.create_index("ix_call_reconciliations_call_id", "call_reconciliations", ["call_id"])


def downgrade() -> None:
    op.drop_table("call_reconciliations")
    op.drop_table("call_pipeline_runs")
    op.drop_table("call_facts")
    op.drop_table("call_intelligence")
    op.drop_table("call_transcript_revisions")
    op.drop_table("call_raw_payloads")
