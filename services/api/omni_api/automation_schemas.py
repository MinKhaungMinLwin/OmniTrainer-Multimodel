from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AutomationCondition(BaseModel):
    field: str = Field(min_length=1, max_length=200)
    operator: Literal["equals", "not_equals", "contains", "exists", "gt", "gte", "lt", "lte"]
    value: Any = None


class AutomationStep(BaseModel):
    kind: Literal["ai", "extract", "action"]
    operation: Literal[
        "summarize", "classify", "extract", "create_job", "add_job_note", "draft_invoice", "send_message"
    ]
    config: dict[str, Any] = Field(default_factory=dict)


class ApprovalRule(BaseModel):
    mode: Literal["always", "never", "external", "writes"] = "writes"


class TemplateRead(BaseModel):
    key: str
    name: str
    description: str
    trigger_type: str
    conditions: list[AutomationCondition]
    steps: list[AutomationStep]
    approval_rule: ApprovalRule


class AutomationDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    template_key: str | None = Field(default=None, max_length=100)
    mode: Literal["shadow", "production"] = "shadow"
    trigger_type: str = Field(min_length=1, max_length=80)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    conditions: list[AutomationCondition] = Field(default_factory=list, max_length=20)
    steps: list[AutomationStep] = Field(min_length=1, max_length=20)
    approval_rule: ApprovalRule = Field(default_factory=ApprovalRule)
    rate_limit_per_hour: int = Field(default=100, ge=1, le=10000)
    max_attempts: int = Field(default=3, ge=1, le=10)


class AutomationDefinitionUpdate(BaseModel):
    description: str = Field(default="", max_length=5000)
    mode: Literal["shadow", "production"] = "shadow"
    trigger_type: str = Field(min_length=1, max_length=80)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    conditions: list[AutomationCondition] = Field(default_factory=list, max_length=20)
    steps: list[AutomationStep] = Field(min_length=1, max_length=20)
    approval_rule: ApprovalRule = Field(default_factory=ApprovalRule)
    rate_limit_per_hour: int = Field(default=100, ge=1, le=10000)
    max_attempts: int = Field(default=3, ge=1, le=10)


class AutomationVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    trigger_type: str
    trigger_config: dict
    conditions: list
    steps: list
    approval_rule: dict
    rate_limit_per_hour: int
    max_attempts: int
    created_at: datetime


class AutomationDefinitionRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    template_key: str | None
    status: str
    mode: str
    current_version: int
    kill_reason: str | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: AutomationVersionRead


class ActivationRequest(BaseModel):
    mode: Literal["shadow", "production"] = "shadow"


class KillRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class BulkControlRequest(BaseModel):
    definition_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["pause", "kill"]
    reason: str = Field(default="Bulk control", min_length=1, max_length=1000)


class TriggerEventCreate(BaseModel):
    trigger_type: str = Field(min_length=1, max_length=80)
    source: Literal["domain_event", "schedule", "inbound_message", "call", "manual"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class TriggerEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    trigger_type: str
    source: str
    idempotency_key: str
    payload: dict
    occurred_at: datetime
    created_at: datetime


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    event_type: str
    payload: dict
    occurred_at: datetime


class AutomationApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    proposed_actions: list
    decided_actions: list | None
    reviewer_user_id: str | None
    reason: str | None
    decided_at: datetime | None


class AutomationRunRead(BaseModel):
    id: str
    tenant_id: str
    definition_id: str
    definition_name: str
    version: int
    trigger_event_id: str
    replay_of_run_id: str | None
    status: str
    mode: str
    reason: str
    input: dict
    output: dict
    changed_resources: list
    attempt: int
    max_attempts: int
    next_retry_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    events: list[RunEventRead] = Field(default_factory=list)
    approval: AutomationApprovalRead | None = None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    actions: list[dict[str, Any]] | None = None
    reason: str = Field(default="", max_length=1000)


class TestRunRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class AutomationMetricsRead(BaseModel):
    total: int
    succeeded: int
    shadowed: int
    waiting_approval: int
    failed: int
    dead_letter: int
    skipped: int
    success_rate: float
    changed_resources: int


class CompensationRead(BaseModel):
    run_id: str
    status: str
    compensated_resources: list[dict]


class TemplateInstallRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("name cannot be empty")
        return value
