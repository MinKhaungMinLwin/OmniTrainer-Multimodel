import os
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from services.api.omni_api.automation_engine import execute_run
from services.api.omni_api.call_pipeline import process_call
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import AutomationRun


class JobEnvelope(BaseModel):
    id: str
    type: Literal["audit.export", "health.check", "automation.execute", "call.process"]
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
        # Reserved for exporting immutable audit batches to an external warehouse.
        return
    if job.type == "automation.execute":
        database_url = os.getenv("OMNI_DATABASE_URL", "sqlite+aiosqlite:///./omni.db")
        engine = build_engine(database_url)
        session_factory = build_session_factory(engine)
        try:
            async with session_factory() as session:
                run_id = str(job.payload["run_id"])
                run = await session.scalar(
                    select(AutomationRun).where(
                        AutomationRun.id == run_id,
                        AutomationRun.tenant_id == job.tenant_id,
                    )
                )
                if run is None:
                    raise ValueError("Automation run not found")
                outcome = await execute_run(session, run.id)
                await session.commit()
                if outcome.status == "retrying":
                    raise RuntimeError(outcome.error_message or "Automation action failed")
        finally:
            await engine.dispose()
        return
    if job.type == "call.process":
        database_url = os.getenv("OMNI_DATABASE_URL", "sqlite+aiosqlite:///./omni.db")
        engine = build_engine(database_url)
        session_factory = build_session_factory(engine)
        try:
            async with session_factory() as session:
                await process_call(session, str(job.payload["call_id"]))
                await session.commit()
        finally:
            await engine.dispose()
        return
    raise ValueError(f"Unsupported job type: {job.type}")
