from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    id: str
    source_type: str
    source_id: str
    title: str
    excerpt: str
    uri: str | None = None


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    run_id: str | None
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    parts: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    created_at: datetime


class RunCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    parent_run_id: str | None = None


class AudioTranscriptionRead(BaseModel):
    text: str
    model: str


class SpeechCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = Field(default=None, min_length=1, max_length=50)


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    user_message_id: str
    parent_run_id: str | None
    status: str
    provider: str
    model: str
    prompt_version: str
    toolset_version: str
    policy_version: str
    knowledge_version: str
    input_tokens: int
    output_tokens: int
    cost_micros: int
    last_sequence: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    sequence: int
    event_type: Literal[
        "run_started",
        "text_delta",
        "tool_proposed",
        "approval_requested",
        "tool_running",
        "tool_result",
        "corrected_result",
        "completed",
        "failed",
        "cancelled",
    ]
    payload: dict[str, Any]
    occurred_at: datetime


class ToolInvocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    name: str
    version: str
    risk: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    tool_invocation_id: str
    status: str
    proposed_args: dict[str, Any]
    decided_args: dict[str, Any] | None
    reason: str | None
    reviewer_user_id: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RunDetail(BaseModel):
    run: RunRead
    events: list[EventRead]
    tools: list[ToolInvocationRead]
    approvals: list[ApprovalRead]


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    arguments: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)


class FeedbackCreate(BaseModel):
    rating: Literal["up", "down"]
    category: (
        Literal[
            "helpful",
            "incorrect",
            "unsafe",
            "missing_context",
            "tool_error",
            "other",
        ]
        | None
    ) = None
    comment: str | None = Field(default=None, max_length=5000)
    message_id: str | None = None


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    rating: str
    category: str | None
    comment: str | None
    run_context: dict[str, Any]
    created_at: datetime


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=500_000)
    source_uri: str | None = Field(default=None, max_length=1000)
    access_roles: list[str] = Field(default_factory=list, max_length=10)


class KnowledgeDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_uri: str | None
    version: int
    status: str
    access_roles: list[str]
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchResult(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    excerpt: str
    source_uri: str | None
    score: float


class ExtractionCreate(BaseModel):
    schema_name: Literal["lead_intake", "job_request", "contact"]
    input_text: str = Field(min_length=1, max_length=50_000)


class ExtractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schema_name: str
    schema_version: str
    input_text: str
    extracted_fields: dict[str, Any]
    confidence_bps: int
    provenance: dict[str, Any]
    status: str
    corrected_fields: dict[str, Any] | None
    reviewer_user_id: str | None
    review_reason: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExtractionReview(BaseModel):
    decision: Literal["accept", "correct", "reject"]
    corrected_fields: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)
