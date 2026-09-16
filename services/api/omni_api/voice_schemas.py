from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceFlowConfigUpdate(BaseModel):
    enabled: bool = False
    pilot_mode: Literal["internal", "limited"] = "internal"
    allowed_hours: dict[str, Any] = Field(
        default_factory=lambda: {
            "timezone": "UTC",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "start": "00:00",
            "end": "23:59",
        }
    )
    max_concurrent_calls: int = Field(default=2, ge=1, le=100)
    allowed_regions: list[str] = Field(default_factory=lambda: ["local"], max_length=20)
    disclosure_text: str = Field(
        default="Hello. I’m an AI assistant for Omni Services. This call may be recorded. Do you consent to continue?",
        min_length=20,
        max_length=1000,
    )
    require_ai_consent: bool = True
    require_recording_consent: bool = True
    retention_days: int = Field(default=30, ge=1, le=3650)
    transfer_number: str | None = Field(default=None, max_length=50)
    emergency_keywords: list[str] = Field(
        default_factory=lambda: ["fire", "gas leak", "medical emergency", "danger"], max_length=100
    )
    allowed_tools: list[Literal["find_customer", "create_callback_job"]] = Field(
        default_factory=lambda: ["find_customer", "create_callback_job"]
    )
    prompt_version: str = Field(default="after-hours-v1", min_length=1, max_length=50)


class VoiceFlowConfigRead(VoiceFlowConfigUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    flow_key: str
    created_at: datetime
    updated_at: datetime


class VoiceCallEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    schema_version: str
    event_type: str
    payload: dict
    occurred_at: datetime


class VoiceTranscriptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    speaker: str
    text: str
    is_final: bool
    start_ms: int
    end_ms: int
    event_sequence: int
    provider: str
    confidence_bps: int | None
    created_at: datetime


class VoiceToolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    input: dict
    output: dict | None
    latency_ms: int
    created_at: datetime


class VoiceReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    action: str
    proposed_args: dict
    decided_args: dict | None
    reviewer_user_id: str | None
    reason: str | None
    decided_at: datetime | None


class VoiceRecordingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    codec: str
    sample_rate_hz: int
    size_bytes: int
    duration_ms: int
    retention_until: datetime
    legal_hold: bool
    created_at: datetime


class VoiceCallRead(BaseModel):
    id: str
    tenant_id: str
    provider: str
    provider_call_id: str
    direction: str
    caller: str
    callee: str
    flow_key: str
    region: str
    status: str
    consent_status: str
    recording_status: str
    customer_id: str | None
    job_id: str | None
    outcome: str | None
    transfer_reason: str | None
    state: dict
    latency_metrics: dict
    started_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    events: list[VoiceCallEventRead] = Field(default_factory=list)
    transcript: list[VoiceTranscriptRead] = Field(default_factory=list)
    tools: list[VoiceToolRead] = Field(default_factory=list)
    review: VoiceReviewRead | None = None
    recording: VoiceRecordingRead | None = None


class VoiceReviewDecision(BaseModel):
    decision: Literal["approve", "reject"]
    args: dict[str, Any] | None = None
    reason: str = Field(default="", max_length=1000)


class VoiceTransferRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class VoiceSimulationTurn(BaseModel):
    type: Literal["transcript", "dtmf", "silence", "voicemail", "speech_start", "hangup"] = "transcript"
    text: str | None = Field(default=None, max_length=5000)
    digit: str | None = Field(default=None, max_length=1)
    confidence_bps: int = Field(default=9900, ge=0, le=10000)
    duration_ms: int = Field(default=500, ge=0, le=60000)
    heard_response_boundary_ms: int = Field(default=0, ge=0)


class VoiceSimulationCreate(BaseModel):
    caller: str = Field(default="+15550100", min_length=1, max_length=100)
    callee: str = Field(default="+15550999", min_length=1, max_length=100)
    region: str = Field(default="local", min_length=1, max_length=50)
    turns: list[VoiceSimulationTurn] = Field(min_length=1, max_length=50)


class VoiceMetricsRead(BaseModel):
    total_calls: int
    active_calls: int
    transferred: int
    review_pending: int
    completed: int
    consent_rate: float
    transfer_rate: float
    average_first_audio_ms: float
    target_first_audio_ms: int
    within_latency_target: bool
