"""Add the after-hours realtime voice pilot records."""

import sqlalchemy as sa
from alembic import op

revision = "0006_voice_pilot"
down_revision = "0005_automations"
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
        "voice_flow_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("flow_key", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("pilot_mode", sa.String(30), nullable=False),
        sa.Column("allowed_hours", sa.JSON(), nullable=False),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False),
        sa.Column("allowed_regions", sa.JSON(), nullable=False),
        sa.Column("disclosure_text", sa.Text(), nullable=False),
        sa.Column("require_ai_consent", sa.Boolean(), nullable=False),
        sa.Column("require_recording_consent", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("transfer_number", sa.String(50)),
        sa.Column("emergency_keywords", sa.JSON(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "flow_key", name="uq_voice_flow_configs_tenant_id"),
    )
    op.create_index("ix_voice_flow_configs_tenant_id", "voice_flow_configs", ["tenant_id"])
    op.create_table(
        "voice_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "flow_config_id",
            sa.String(36),
            sa.ForeignKey("voice_flow_configs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_call_id", sa.String(200), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("caller", sa.String(100), nullable=False),
        sa.Column("callee", sa.String(100), nullable=False),
        sa.Column("flow_key", sa.String(100), nullable=False),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("consent_status", sa.String(30), nullable=False),
        sa.Column("recording_status", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("outcome", sa.String(80)),
        sa.Column("transfer_reason", sa.Text()),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("latency_metrics", sa.JSON(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("provider", "provider_call_id", name="uq_voice_calls_provider"),
    )
    op.create_index("ix_voice_calls_tenant_id", "voice_calls", ["tenant_id"])
    op.create_index("ix_voice_calls_flow_config_id", "voice_calls", ["flow_config_id"])
    op.create_index("ix_voice_calls_tenant_status", "voice_calls", ["tenant_id", "status"])
    op.create_table(
        "voice_call_events",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("call_id", "sequence", name="uq_voice_call_events_call_id"),
    )
    op.create_index("ix_voice_call_events_tenant_id", "voice_call_events", ["tenant_id"])
    op.create_index("ix_voice_call_events_call_id", "voice_call_events", ["call_id"])
    op.create_index("ix_voice_call_events_call_sequence", "voice_call_events", ["call_id", "sequence"])
    op.create_table(
        "voice_transcript_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("confidence_bps", sa.Integer()),
        *timestamps(),
        sa.UniqueConstraint("call_id", "sequence", name="uq_voice_transcript_segments_call_id"),
    )
    op.create_index("ix_voice_transcript_segments_tenant_id", "voice_transcript_segments", ["tenant_id"])
    op.create_index("ix_voice_transcript_segments_call_id", "voice_transcript_segments", ["call_id"])
    op.create_table(
        "voice_tool_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_voice_tool_invocations_tenant_id", "voice_tool_invocations", ["tenant_id"])
    op.create_index("ix_voice_tool_invocations_call_id", "voice_tool_invocations", ["call_id"])
    op.create_table(
        "voice_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("proposed_args", sa.JSON(), nullable=False),
        sa.Column("decided_args", sa.JSON()),
        sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_voice_reviews_tenant_id", "voice_reviews", ["tenant_id"])
    op.create_index("ix_voice_reviews_call_id", "voice_reviews", ["call_id"])
    op.create_table(
        "voice_recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "call_id", sa.String(36), sa.ForeignKey("voice_calls.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("object_key", sa.String(500), nullable=False, unique=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("codec", sa.String(30), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_voice_recordings_tenant_id", "voice_recordings", ["tenant_id"])
    op.create_index("ix_voice_recordings_call_id", "voice_recordings", ["call_id"])


def downgrade() -> None:
    op.drop_table("voice_recordings")
    op.drop_table("voice_reviews")
    op.drop_table("voice_tool_invocations")
    op.drop_table("voice_transcript_segments")
    op.drop_table("voice_call_events")
    op.drop_table("voice_calls")
    op.drop_table("voice_flow_configs")
