"""Add durable AI copilot, knowledge, review, and extraction records."""

import sqlalchemy as sa
from alembic import op

revision = "0004_ai_copilot"
down_revision = "0003_finish_operations_mvp"
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
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_tenant_updated", "conversations", ["tenant_id", "updated_at"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36)),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_conversation_messages_tenant_id", "conversation_messages", ["tenant_id"])
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])
    op.create_index("ix_conversation_messages_run_id", "conversation_messages", ["run_id"])
    op.create_index("ix_messages_conversation_created", "conversation_messages", ["conversation_id", "created_at"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="SET NULL")),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("toolset_version", sa.String(50), nullable=False),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("knowledge_version", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_runs_tenant_id"),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_user_message_id", "agent_runs", ["user_message_id"])
    op.create_index("ix_agent_runs_tenant_status", "agent_runs", ["tenant_id", "status"])
    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_id"),
    )
    op.create_index("ix_agent_events_tenant_id", "agent_events", ["tenant_id"])
    op.create_index("ix_agent_events_run_id", "agent_events", ["run_id"])
    op.create_index("ix_agent_events_run_sequence", "agent_events", ["run_id", "sequence"])
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(30), nullable=False),
        sa.Column("risk", sa.String(30), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_tool_invocations_tenant_id"),
    )
    op.create_index("ix_tool_invocations_tenant_id", "tool_invocations", ["tenant_id"])
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "tool_invocation_id",
            sa.String(36),
            sa.ForeignKey("tool_invocations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("proposed_args", sa.JSON(), nullable=False),
        sa.Column("decided_args", sa.JSON()),
        sa.Column("reason", sa.Text()),
        sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_index("ix_approval_requests_tool_invocation_id", "approval_requests", ["tool_invocation_id"])
    op.create_table(
        "agent_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("category", sa.String(80)),
        sa.Column("comment", sa.Text()),
        sa.Column("run_context", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_agent_feedback_tenant_id", "agent_feedback", ["tenant_id"])
    op.create_index("ix_agent_feedback_run_id", "agent_feedback", ["run_id"])
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("source_uri", sa.String(1000)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("access_roles", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_knowledge_documents_tenant_id"),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunks_document_id"),
    )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("schema_name", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.String(30), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("extracted_fields", sa.JSON(), nullable=False),
        sa.Column("confidence_bps", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("corrected_fields", sa.JSON()),
        sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("review_reason", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_extraction_runs_tenant_id", "extraction_runs", ["tenant_id"])
    op.create_index("ix_extractions_tenant_status", "extraction_runs", ["tenant_id", "status"])
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        tenant_id(),
        sa.Column("requested_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        *timestamps(),
    )
    op.create_index("ix_outbound_messages_tenant_id", "outbound_messages", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("outbound_messages")
    op.drop_table("extraction_runs")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("agent_feedback")
    op.drop_table("approval_requests")
    op.drop_table("tool_invocations")
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.drop_table("conversation_messages")
    op.drop_table("conversations")
