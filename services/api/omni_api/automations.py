from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.audit import add_audit_event
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.automation_engine import (
    AUTOMATION_TEMPLATES,
    append_run_event,
    compensate_run,
    create_runs_for_trigger,
    execute_run,
)
from services.api.omni_api.automation_schemas import (
    ActivationRequest,
    ApprovalDecision,
    AutomationDefinitionCreate,
    AutomationDefinitionRead,
    AutomationDefinitionUpdate,
    AutomationMetricsRead,
    AutomationRunRead,
    AutomationVersionRead,
    BulkControlRequest,
    CompensationRead,
    KillRequest,
    TemplateInstallRequest,
    TemplateRead,
    TestRunRequest,
    TriggerEventCreate,
)
from services.api.omni_api.database import get_session
from services.api.omni_api.models import (
    AutomationApproval,
    AutomationDeadLetter,
    AutomationDefinition,
    AutomationRun,
    AutomationRunEvent,
    AutomationTriggerEvent,
    AutomationVersion,
)

router = APIRouter(prefix="/automations", tags=["automations"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def version_read(version: AutomationVersion) -> AutomationVersionRead:
    return AutomationVersionRead.model_validate(version)


async def tenant_definition(session: AsyncSession, tenant_id: str, definition_id: str) -> AutomationDefinition:
    definition = await session.scalar(
        select(AutomationDefinition).where(
            AutomationDefinition.id == definition_id,
            AutomationDefinition.tenant_id == tenant_id,
        )
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return definition


async def current_version(session: AsyncSession, definition: AutomationDefinition) -> AutomationVersion:
    version = await session.scalar(
        select(AutomationVersion).where(
            AutomationVersion.definition_id == definition.id,
            AutomationVersion.version == definition.current_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=409, detail="Automation version is missing")
    return version


async def definition_read(session: AsyncSession, definition: AutomationDefinition) -> AutomationDefinitionRead:
    version = await current_version(session, definition)
    return AutomationDefinitionRead(
        id=definition.id,
        tenant_id=definition.tenant_id,
        name=definition.name,
        description=definition.description,
        template_key=definition.template_key,
        status=definition.status,
        mode=definition.mode,
        current_version=definition.current_version,
        kill_reason=definition.kill_reason,
        activated_at=definition.activated_at,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
        version=version_read(version),
    )


async def tenant_run(session: AsyncSession, tenant_id: str, run_id: str) -> AutomationRun:
    run = await session.scalar(
        select(AutomationRun).where(AutomationRun.id == run_id, AutomationRun.tenant_id == tenant_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    return run


async def run_read(session: AsyncSession, run: AutomationRun, include_events: bool = True) -> AutomationRunRead:
    definition = await session.get(AutomationDefinition, run.definition_id)
    version = await session.get(AutomationVersion, run.version_id)
    events = (
        list(
            await session.scalars(
                select(AutomationRunEvent)
                .where(AutomationRunEvent.run_id == run.id)
                .order_by(AutomationRunEvent.sequence)
            )
        )
        if include_events
        else []
    )
    approval = await session.scalar(select(AutomationApproval).where(AutomationApproval.run_id == run.id))
    return AutomationRunRead(
        id=run.id,
        tenant_id=run.tenant_id,
        definition_id=run.definition_id,
        definition_name=definition.name if definition else "Deleted automation",
        version=version.version if version else 0,
        trigger_event_id=run.trigger_event_id,
        replay_of_run_id=run.replay_of_run_id,
        status=run.status,
        mode=run.mode,
        reason=run.reason,
        input=run.input,
        output=run.output,
        changed_resources=run.changed_resources,
        attempt=run.attempt,
        max_attempts=run.max_attempts,
        next_retry_at=run.next_retry_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
        events=events,
        approval=approval,
    )


def audit_control(
    session: AsyncSession,
    context: TenantContext,
    request: Request,
    definition: AutomationDefinition,
    action: str,
    payload: dict | None = None,
) -> None:
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action=action,
        resource_type="automation",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        payload=payload,
    )


@router.get("/templates", response_model=list[TemplateRead])
async def templates(_: TenantContext = Depends(get_tenant_context)) -> list[TemplateRead]:
    return [TemplateRead(key=key, **template) for key, template in AUTOMATION_TEMPLATES.items()]


@router.get("", response_model=list[AutomationDefinitionRead])
async def definitions(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationDefinitionRead]:
    records = list(
        await session.scalars(
            select(AutomationDefinition)
            .where(AutomationDefinition.tenant_id == context.tenant_id)
            .order_by(AutomationDefinition.updated_at.desc())
        )
    )
    return [await definition_read(session, item) for item in records]


@router.post("", response_model=AutomationDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_definition(
    payload: AutomationDefinitionCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    definition = AutomationDefinition(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        name=payload.name,
        description=payload.description,
        template_key=payload.template_key,
        status="draft",
        mode=payload.mode,
        current_version=1,
    )
    session.add(definition)
    await session.flush()
    version = AutomationVersion(
        tenant_id=context.tenant_id,
        definition_id=definition.id,
        version=1,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        conditions=[item.model_dump() for item in payload.conditions],
        steps=[item.model_dump() for item in payload.steps],
        approval_rule=payload.approval_rule.model_dump(),
        rate_limit_per_hour=payload.rate_limit_per_hour,
        max_attempts=payload.max_attempts,
        created_by_user_id=context.user.id,
    )
    session.add(version)
    audit_control(session, context, request, definition, "automation.created", {"version": 1})
    await session.commit()
    await session.refresh(definition)
    return await definition_read(session, definition)


@router.post(
    "/templates/{template_key}/install",
    response_model=AutomationDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def install_template(
    template_key: str,
    payload: TemplateInstallRequest,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    template = AUTOMATION_TEMPLATES.get(template_key)
    if template is None:
        raise HTTPException(status_code=404, detail="Automation template not found")
    create = AutomationDefinitionCreate(
        name=payload.name or template["name"],
        description=template["description"],
        template_key=template_key,
        mode="shadow",
        trigger_type=template["trigger_type"],
        conditions=template["conditions"],
        steps=template["steps"],
        approval_rule=template["approval_rule"],
    )
    return await create_definition(create, request, context, session)


@router.get("/definitions/{definition_id}", response_model=AutomationDefinitionRead)
async def get_definition(
    definition_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    return await definition_read(session, await tenant_definition(session, context.tenant_id, definition_id))


@router.put("/definitions/{definition_id}", response_model=AutomationDefinitionRead)
async def update_definition(
    definition_id: str,
    payload: AutomationDefinitionUpdate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    definition = await tenant_definition(session, context.tenant_id, definition_id)
    if definition.status == "killed":
        raise HTTPException(status_code=409, detail="Killed automations cannot be edited; install a new copy")
    definition.current_version += 1
    definition.description = payload.description
    definition.mode = payload.mode
    definition.status = "draft"
    session.add(
        AutomationVersion(
            tenant_id=context.tenant_id,
            definition_id=definition.id,
            version=definition.current_version,
            trigger_type=payload.trigger_type,
            trigger_config=payload.trigger_config,
            conditions=[item.model_dump() for item in payload.conditions],
            steps=[item.model_dump() for item in payload.steps],
            approval_rule=payload.approval_rule.model_dump(),
            rate_limit_per_hour=payload.rate_limit_per_hour,
            max_attempts=payload.max_attempts,
            created_by_user_id=context.user.id,
        )
    )
    audit_control(
        session, context, request, definition, "automation.version_created", {"version": definition.current_version}
    )
    await session.commit()
    await session.refresh(definition)
    return await definition_read(session, definition)


@router.post("/definitions/{definition_id}/activate", response_model=AutomationDefinitionRead)
async def activate_definition(
    definition_id: str,
    payload: ActivationRequest,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    definition = await tenant_definition(session, context.tenant_id, definition_id)
    if definition.status == "killed":
        raise HTTPException(status_code=409, detail="Killed automations cannot be reactivated")
    definition.status = "active"
    definition.mode = payload.mode
    definition.activated_at = now()
    audit_control(session, context, request, definition, "automation.activated", {"mode": payload.mode})
    await session.commit()
    await session.refresh(definition)
    return await definition_read(session, definition)


@router.post("/definitions/{definition_id}/pause", response_model=AutomationDefinitionRead)
async def pause_definition(
    definition_id: str,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    definition = await tenant_definition(session, context.tenant_id, definition_id)
    if definition.status != "killed":
        definition.status = "paused"
    audit_control(session, context, request, definition, "automation.paused")
    await session.commit()
    await session.refresh(definition)
    return await definition_read(session, definition)


@router.post("/definitions/{definition_id}/kill", response_model=AutomationDefinitionRead)
async def kill_definition(
    definition_id: str,
    payload: KillRequest,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> AutomationDefinitionRead:
    definition = await tenant_definition(session, context.tenant_id, definition_id)
    definition.status = "killed"
    definition.kill_reason = payload.reason
    queued = list(
        await session.scalars(
            select(AutomationRun).where(
                AutomationRun.definition_id == definition.id,
                AutomationRun.status.in_(["queued", "retrying", "waiting_approval"]),
            )
        )
    )
    for run in queued:
        run.status = "cancelled"
        run.reason = f"Kill switch: {payload.reason}"
        run.completed_at = now()
        await append_run_event(session, run, "run_cancelled", {"reason": payload.reason})
    audit_control(session, context, request, definition, "automation.killed", {"reason": payload.reason})
    await session.commit()
    await session.refresh(definition)
    return await definition_read(session, definition)


@router.post("/bulk/control", response_model=list[AutomationDefinitionRead])
async def bulk_control(
    payload: BulkControlRequest,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationDefinitionRead]:
    definitions = list(
        await session.scalars(
            select(AutomationDefinition).where(
                AutomationDefinition.tenant_id == context.tenant_id,
                AutomationDefinition.id.in_(payload.definition_ids),
            )
        )
    )
    if len(definitions) != len(set(payload.definition_ids)):
        raise HTTPException(status_code=404, detail="One or more automations were not found")
    for definition in definitions:
        if definition.status == "killed":
            continue
        definition.status = "killed" if payload.action == "kill" else "paused"
        if payload.action == "kill":
            definition.kill_reason = payload.reason
        active_runs = list(
            await session.scalars(
                select(AutomationRun).where(
                    AutomationRun.definition_id == definition.id,
                    AutomationRun.status.in_(["queued", "retrying", "waiting_approval"]),
                )
            )
        )
        for run in active_runs:
            run.status = "cancelled"
            run.reason = f"Bulk {payload.action}: {payload.reason}"
            run.completed_at = now()
            await append_run_event(session, run, "run_cancelled", {"reason": payload.reason})
        audit_control(
            session,
            context,
            request,
            definition,
            "automation.killed" if payload.action == "kill" else "automation.paused",
            {"reason": payload.reason, "bulk": True},
        )
    await session.commit()
    return [await definition_read(session, definition) for definition in definitions]


@router.post("/triggers", response_model=list[AutomationRunRead])
async def ingest_trigger(
    payload: TriggerEventCreate,
    context: TenantContext = Depends(require_roles("owner", "administrator", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationRunRead]:
    event = await session.scalar(
        select(AutomationTriggerEvent).where(
            AutomationTriggerEvent.tenant_id == context.tenant_id,
            AutomationTriggerEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if event is None:
        event = AutomationTriggerEvent(
            tenant_id=context.tenant_id,
            trigger_type=payload.trigger_type,
            source=payload.source,
            idempotency_key=payload.idempotency_key,
            payload=payload.payload,
            occurred_at=payload.occurred_at or now(),
        )
        session.add(event)
        await session.flush()
        runs = await create_runs_for_trigger(session, event)
        for run in runs:
            if run.status == "queued":
                await execute_run(session, run.id)
        await session.commit()
    else:
        runs = list(await session.scalars(select(AutomationRun).where(AutomationRun.trigger_event_id == event.id)))
    return [await run_read(session, run) for run in runs]


@router.post("/definitions/{definition_id}/test", response_model=AutomationRunRead)
async def test_definition(
    definition_id: str,
    payload: TestRunRequest,
    context: TenantContext = Depends(require_roles("owner", "administrator", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> AutomationRunRead:
    definition = await tenant_definition(session, context.tenant_id, definition_id)
    version = await current_version(session, definition)
    event = AutomationTriggerEvent(
        tenant_id=context.tenant_id,
        trigger_type=version.trigger_type,
        source="manual",
        idempotency_key=f"test:{definition.id}:{now().timestamp()}",
        payload={**payload.payload, "_test_mode": True},
        occurred_at=now(),
    )
    session.add(event)
    await session.flush()
    run = AutomationRun(
        tenant_id=context.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
        trigger_event_id=event.id,
        status="queued",
        mode="shadow",
        reason="Manual test using an immutable version snapshot",
        input=event.payload,
        max_attempts=version.max_attempts,
    )
    session.add(run)
    await session.flush()
    await append_run_event(session, run, "test_started", {"version": version.version})
    await execute_run(session, run.id)
    await session.commit()
    return await run_read(session, run)


@router.get("/runs/history", response_model=list[AutomationRunRead])
async def runs(
    run_status: str | None = None,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[AutomationRunRead]:
    statement = select(AutomationRun).where(AutomationRun.tenant_id == context.tenant_id)
    if run_status:
        statement = statement.where(AutomationRun.status == run_status)
    records = list(await session.scalars(statement.order_by(AutomationRun.created_at.desc()).limit(200)))
    return [await run_read(session, item, include_events=False) for item in records]


@router.get("/runs/{run_id}", response_model=AutomationRunRead)
async def get_run(
    run_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> AutomationRunRead:
    return await run_read(session, await tenant_run(session, context.tenant_id, run_id))


@router.post("/runs/{run_id}/retry", response_model=AutomationRunRead)
async def retry_run(
    run_id: str,
    context: TenantContext = Depends(require_roles("owner", "administrator", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> AutomationRunRead:
    run = await tenant_run(session, context.tenant_id, run_id)
    if run.status not in {"retrying", "dead_letter"}:
        raise HTTPException(status_code=409, detail="Only retrying or dead-letter runs can be retried")
    dead_letter = await session.scalar(select(AutomationDeadLetter).where(AutomationDeadLetter.run_id == run.id))
    if dead_letter is not None:
        await session.delete(dead_letter)
        await session.flush()
        run.attempt = 0
    run.status = "retrying"
    run.next_retry_at = now()
    await execute_run(session, run.id)
    await session.commit()
    return await run_read(session, run)


@router.post("/runs/{run_id}/replay", response_model=AutomationRunRead, status_code=status.HTTP_201_CREATED)
async def replay_run(
    run_id: str,
    context: TenantContext = Depends(require_roles("owner", "administrator", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> AutomationRunRead:
    original = await tenant_run(session, context.tenant_id, run_id)
    original_event = await session.get(AutomationTriggerEvent, original.trigger_event_id)
    event = AutomationTriggerEvent(
        tenant_id=context.tenant_id,
        trigger_type=original_event.trigger_type,
        source="manual",
        idempotency_key=f"replay:{original.id}:{now().timestamp()}",
        payload=original.input,
        occurred_at=now(),
    )
    session.add(event)
    await session.flush()
    replay = AutomationRun(
        tenant_id=context.tenant_id,
        definition_id=original.definition_id,
        version_id=original.version_id,
        trigger_event_id=event.id,
        replay_of_run_id=original.id,
        status="queued",
        mode=original.mode,
        reason=f"Replay of {original.id} using the same version",
        input=original.input,
        max_attempts=original.max_attempts,
    )
    session.add(replay)
    await session.flush()
    await append_run_event(session, replay, "run_replayed", {"original_run_id": original.id})
    await execute_run(session, replay.id)
    await session.commit()
    return await run_read(session, replay)


@router.post("/runs/{run_id}/compensate", response_model=CompensationRead)
async def compensate(
    run_id: str,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> CompensationRead:
    run = await tenant_run(session, context.tenant_id, run_id)
    if run.status != "succeeded":
        raise HTTPException(status_code=409, detail="Only successful runs can be compensated")
    resources = await compensate_run(session, run)
    await session.commit()
    return CompensationRead(run_id=run.id, status=run.status, compensated_resources=resources)


@router.post("/approvals/{approval_id}/decision", response_model=AutomationRunRead)
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    context: TenantContext = Depends(require_roles("owner", "administrator", "reviewer")),
    session: AsyncSession = Depends(get_session),
) -> AutomationRunRead:
    approval = await session.scalar(
        select(AutomationApproval).where(
            AutomationApproval.id == approval_id,
            AutomationApproval.tenant_id == context.tenant_id,
        )
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Automation approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Approval has already been decided")
    run = await tenant_run(session, context.tenant_id, approval.run_id)
    approval.reviewer_user_id = context.user.id
    approval.reason = payload.reason
    approval.decided_at = now()
    if payload.decision == "reject":
        approval.status = "rejected"
        run.status = "rejected"
        run.reason = payload.reason or "Human reviewer rejected proposed actions"
        run.completed_at = now()
        await append_run_event(session, run, "approval_rejected", {"reason": payload.reason})
    else:
        actions = payload.actions or approval.proposed_actions
        allowed_operations = {item.get("operation") for item in approval.proposed_actions}
        if any(item.get("operation") not in allowed_operations for item in actions):
            raise HTTPException(status_code=422, detail="Edited actions may not add new operation types")
        approval.status = "approved"
        approval.decided_actions = actions
        await append_run_event(session, run, "approval_granted", {"edited": payload.actions is not None})
        await execute_run(session, run.id, actions)
    await session.commit()
    return await run_read(session, run)


@router.get("/metrics/summary", response_model=AutomationMetricsRead)
async def metrics(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> AutomationMetricsRead:
    rows = (
        await session.execute(
            select(AutomationRun.status, func.count(AutomationRun.id))
            .where(AutomationRun.tenant_id == context.tenant_id)
            .group_by(AutomationRun.status)
        )
    ).all()
    counts = {key: value for key, value in rows}
    total = sum(counts.values())
    succeeded = counts.get("succeeded", 0)
    shadowed = counts.get("shadowed", 0)
    changed_resources = list(
        await session.scalars(
            select(AutomationRun.changed_resources).where(AutomationRun.tenant_id == context.tenant_id)
        )
    )
    return AutomationMetricsRead(
        total=total,
        succeeded=succeeded,
        shadowed=shadowed,
        waiting_approval=counts.get("waiting_approval", 0),
        failed=counts.get("retrying", 0),
        dead_letter=counts.get("dead_letter", 0),
        skipped=counts.get("skipped", 0) + counts.get("rate_limited", 0),
        success_rate=round((succeeded + shadowed) / total, 4) if total else 0,
        changed_resources=sum(len(resources) for resources in changed_resources),
    )
