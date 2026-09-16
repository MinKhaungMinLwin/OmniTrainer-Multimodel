from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobEnvelope(BaseModel):
    id: str
    type: Literal["audit.export", "health.check"]
    tenant_id: str
    correlation_id: str
    idempotency_key: str
    attempt: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


async def execute_job(job: JobEnvelope) -> None:
    if job.type == "health.check":
        return
    if job.type == "audit.export":
        # The milestone proves typed dispatch and reliability boundaries. The
        # warehouse exporter will replace this no-op in the call-data phase.
        return
    raise ValueError(f"Unsupported job type: {job.type}")
