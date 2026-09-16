import os
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import func, select

from services.api.omni_api.automation_engine import append_run_event, create_runs_for_trigger
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import (
    AutomationDefinition,
    AutomationRun,
    AutomationTriggerEvent,
    AutomationVersion,
    OutboxEvent,
)
from services.worker.omni_worker.jobs import JobEnvelope

QUEUE_NAME = "omni:jobs"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_run(redis: Redis, run: AutomationRun) -> bool:
    claimed = await redis.set(f"omni:automation:dispatch:{run.id}", "1", nx=True, ex=60)
    if not claimed:
        return False
    envelope = JobEnvelope(
        id=f"automation-{run.id}",
        type="automation.execute",
        tenant_id=run.tenant_id,
        correlation_id=f"automation:{run.id}",
        idempotency_key=f"automation-run:{run.id}",
        payload={"run_id": run.id},
    )
    await redis.rpush(QUEUE_NAME, envelope.model_dump_json())
    return True


async def enqueue_call(redis: Redis, tenant_id: str, call_id: str, correlation_id: str) -> bool:
    claimed = await redis.set(f"omni:call-pipeline:dispatch:{call_id}", "1", nx=True, ex=60)
    if not claimed:
        return False
    envelope = JobEnvelope(
        id=f"call-pipeline-{call_id}",
        type="call.process",
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        idempotency_key=f"call-pipeline:{call_id}",
        payload={"call_id": call_id},
    )
    await redis.rpush(QUEUE_NAME, envelope.model_dump_json())
    return True


async def dispatch_automation_work(redis: Redis) -> int:
    """Bridge transactional domain events and durable runs into the Redis work queue.

    Trigger and run uniqueness make this safe to repeat after a process crash. Redis
    claims only reduce duplicate queue traffic; the database remains the source of truth.
    """

    engine = build_engine(os.getenv("OMNI_DATABASE_URL", "sqlite+aiosqlite:///./omni.db"))
    session_factory = build_session_factory(engine)
    dispatched = 0
    try:
        async with session_factory() as session:
            outbox = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at)
                    .limit(100)
                )
            )
            runs_to_enqueue: dict[str, AutomationRun] = {}
            calls_to_enqueue: dict[str, tuple[str, str]] = {}
            for record in outbox:
                if record.event_type == "voice.call.ended":
                    calls_to_enqueue[record.aggregate_id] = (record.tenant_id, record.correlation_id)
                idempotency_key = f"outbox:{record.id}"
                event = await session.scalar(
                    select(AutomationTriggerEvent).where(
                        AutomationTriggerEvent.tenant_id == record.tenant_id,
                        AutomationTriggerEvent.idempotency_key == idempotency_key,
                    )
                )
                if event is None:
                    event = AutomationTriggerEvent(
                        tenant_id=record.tenant_id,
                        trigger_type=record.event_type,
                        source="domain_event",
                        idempotency_key=idempotency_key,
                        payload={
                            **record.payload,
                            f"{record.aggregate_type}_id": record.aggregate_id,
                            "aggregate_type": record.aggregate_type,
                            "aggregate_id": record.aggregate_id,
                        },
                        occurred_at=record.occurred_at,
                    )
                    session.add(event)
                    await session.flush()
                    runs = await create_runs_for_trigger(session, event)
                else:
                    runs = list(
                        await session.scalars(select(AutomationRun).where(AutomationRun.trigger_event_id == event.id))
                    )
                for run in runs:
                    if run.status in {"queued", "retrying"}:
                        runs_to_enqueue[run.id] = run
                record.published_at = utcnow()

            active_versions = (
                await session.execute(
                    select(AutomationDefinition, AutomationVersion)
                    .join(
                        AutomationVersion,
                        (AutomationVersion.definition_id == AutomationDefinition.id)
                        & (AutomationVersion.version == AutomationDefinition.current_version),
                    )
                    .where(AutomationDefinition.status == "active")
                )
            ).all()
            epoch_minutes = int(utcnow().timestamp() // 60)
            for definition, version in active_versions:
                interval = version.trigger_config.get("interval_minutes")
                if not isinstance(interval, int) or interval < 1:
                    continue
                bucket = epoch_minutes // interval
                key = f"schedule:{definition.id}:{bucket}"
                event = await session.scalar(
                    select(AutomationTriggerEvent).where(
                        AutomationTriggerEvent.tenant_id == definition.tenant_id,
                        AutomationTriggerEvent.idempotency_key == key,
                    )
                )
                if event is not None:
                    continue
                event = AutomationTriggerEvent(
                    tenant_id=definition.tenant_id,
                    trigger_type=version.trigger_type,
                    source="schedule",
                    idempotency_key=key,
                    payload={**version.trigger_config.get("payload", {}), "scheduled_at": utcnow().isoformat()},
                    occurred_at=utcnow(),
                )
                session.add(event)
                await session.flush()
                recent = await session.scalar(
                    select(func.count(AutomationRun.id)).where(
                        AutomationRun.definition_id == definition.id,
                        AutomationRun.created_at >= utcnow() - timedelta(hours=1),
                    )
                )
                run_status = "rate_limited" if (recent or 0) >= version.rate_limit_per_hour else "queued"
                run = AutomationRun(
                    tenant_id=definition.tenant_id,
                    definition_id=definition.id,
                    version_id=version.id,
                    trigger_event_id=event.id,
                    status=run_status,
                    mode=definition.mode,
                    reason=f"Scheduled every {interval} minute(s)",
                    input=event.payload,
                    max_attempts=version.max_attempts,
                )
                session.add(run)
                await session.flush()
                await append_run_event(
                    session,
                    run,
                    "rate_limited" if run_status == "rate_limited" else "schedule_fired",
                    {"interval_minutes": interval},
                )
                if run_status == "queued":
                    runs_to_enqueue[run.id] = run

            due_runs = list(
                await session.scalars(
                    select(AutomationRun)
                    .where(
                        AutomationRun.status.in_(["queued", "retrying"]),
                        (AutomationRun.next_retry_at.is_(None)) | (AutomationRun.next_retry_at <= utcnow()),
                    )
                    .limit(100)
                )
            )
            for run in due_runs:
                runs_to_enqueue[run.id] = run
            await session.commit()
            for run in runs_to_enqueue.values():
                if await enqueue_run(redis, run):
                    dispatched += 1
            for call_id, (tenant_id, correlation_id) in calls_to_enqueue.items():
                if await enqueue_call(redis, tenant_id, call_id, correlation_id):
                    dispatched += 1
    finally:
        await engine.dispose()
    return dispatched
