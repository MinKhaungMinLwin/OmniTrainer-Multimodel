from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegmentCorrection(BaseModel):
    source_segment_id: str | None = None
    speaker: Literal["caller", "assistant", "agent", "system"]
    text: str = Field(min_length=1, max_length=10_000)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    event_sequence: int = Field(default=0, ge=0)


class TranscriptCorrection(BaseModel):
    segments: list[TranscriptSegmentCorrection] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class TranscriptRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_id: str
    source_revision_id: str | None
    version: int
    status: str
    segments: list
    redacted_text: str
    content_hash: str
    processor_version: str
    correction_reason: str | None
    created_at: datetime


class IntelligenceCorrections(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    intent: str | None = Field(default=None, min_length=1, max_length=100)
    summary: str | None = Field(default=None, min_length=1, max_length=10_000)
    outcome: str | None = Field(default=None, min_length=1, max_length=100)
    action_items: list[str] | None = None
    compliance_flags: list[dict] | None = None
    structured_fields: dict | None = None
    sentiment_trajectory: list[dict] | None = None
    objections: list[str] | None = None


class IntelligenceReview(BaseModel):
    decision: Literal["accept", "correct", "reject"]
    corrected_fields: IntelligenceCorrections | None = None
    reason: str = Field(default="", max_length=1000)


class IntelligenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_id: str
    transcript_revision_id: str
    version: int
    extractor_version: str
    topic: str
    intent: str
    sentiment_trajectory: list
    objections: list
    compliance_flags: list
    action_items: list
    summary: str
    outcome: str
    structured_fields: dict
    confidence_bps: int
    provenance: dict
    status: str
    corrected_fields: dict | None
    review_reason: str | None
    reviewed_at: datetime | None
    created_at: datetime


class CallFactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_id: str
    provider: str
    region: str
    flow_key: str
    started_at: datetime
    duration_ms: int
    answered: bool
    contained: bool
    transferred: bool
    abandoned: bool
    consented: bool
    interruption_count: int
    silence_count: int
    tool_count: int
    first_audio_ms: int
    transcript_segments: int
    estimated_cost_microusd: int
    outcome: str


class PipelineRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_id: str
    pipeline_version: str
    status: str
    checkpoints: list
    attempt: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ReconciliationCreate(BaseModel):
    provider_snapshot_id: str = Field(min_length=1, max_length=200)
    provider_status: str = Field(min_length=1, max_length=50)
    provider_duration_ms: int = Field(ge=0)


class ReconciliationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_id: str
    provider_snapshot_id: str
    provider_status: str
    provider_duration_ms: int
    internal_status: str
    internal_duration_ms: int
    complete: bool
    discrepancies: list
    reconciled_at: datetime


class CallIntelligenceDetail(BaseModel):
    intelligence: IntelligenceRead
    transcript: TranscriptRevisionRead
    fact: CallFactRead
    pipeline: PipelineRunRead
    reconciliations: list[ReconciliationRead]


class IntelligenceSearchResult(BaseModel):
    call_id: str
    topic: str
    intent: str
    summary: str
    outcome: str
    confidence_bps: int
    status: str
    started_at: datetime
    snippet: str


class MetricDefinition(BaseModel):
    key: str
    label: str
    definition: str
    unit: str


class IntelligenceDashboard(BaseModel):
    total_calls: int
    answered_rate: float
    containment_rate: float
    transfer_rate: float
    abandonment_rate: float
    consent_rate: float
    average_duration_ms: float
    average_first_audio_ms: float
    estimated_cost_microusd: int
    review_pending: int
    compliance_flagged: int
    reconciliation_rate: float
    pipeline_success_rate: float
    data_freshness_at: datetime | None
    topics: list[dict]
    outcomes: list[dict]
    sentiment: list[dict]
