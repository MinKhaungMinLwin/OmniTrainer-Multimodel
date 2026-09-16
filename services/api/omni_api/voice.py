import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.audit import add_audit_event
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.database import get_session
from services.api.omni_api.models import (
    AuditEvent,
    Customer,
    Job,
    JobStatusHistory,
    OutboxEvent,
    VoiceCall,
    VoiceCallEvent,
    VoiceFlowConfig,
    VoiceRecording,
    VoiceReview,
    VoiceToolInvocation,
    VoiceTranscriptSegment,
)
from services.api.omni_api.storage import AttachmentStorage
from services.api.omni_api.voice_schemas import (
    VoiceCallRead,
    VoiceFlowConfigRead,
    VoiceFlowConfigUpdate,
    VoiceMetricsRead,
    VoiceReviewDecision,
    VoiceSimulationCreate,
    VoiceTransferRequest,
)
from services.voice.omni_voice.runtime import (
    AdmissionError,
    admit_call,
    answer_call,
    append_event,
    handle_dtmf,
    handle_silence,
    handle_user_turn,
    handle_voicemail,
    hangup_call,
    speech_started,
    store_recording,
)

router = APIRouter(prefix="/voice", tags=["voice"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_config(session: AsyncSession, tenant_id: str) -> VoiceFlowConfig:
    config = await session.scalar(
        select(VoiceFlowConfig).where(
            VoiceFlowConfig.tenant_id == tenant_id,
            VoiceFlowConfig.flow_key == "after_hours_intake",
        )
    )
    if config is None:
        defaults = VoiceFlowConfigUpdate()
        config = VoiceFlowConfig(
            tenant_id=tenant_id,
            flow_key="after_hours_intake",
            **defaults.model_dump(),
        )
        session.add(config)
        await session.flush()
    return config


async def tenant_call(session: AsyncSession, tenant_id: str, call_id: str) -> VoiceCall:
    call = await session.scalar(select(VoiceCall).where(VoiceCall.id == call_id, VoiceCall.tenant_id == tenant_id))
    if call is None:
        raise HTTPException(status_code=404, detail="Voice call not found")
    return call


async def call_read(session: AsyncSession, call: VoiceCall, details: bool = True) -> VoiceCallRead:
    events = (
        list(
            await session.scalars(
                select(VoiceCallEvent).where(VoiceCallEvent.call_id == call.id).order_by(VoiceCallEvent.sequence)
            )
        )
        if details
        else []
    )
    transcript = (
        list(
            await session.scalars(
                select(VoiceTranscriptSegment)
                .where(VoiceTranscriptSegment.call_id == call.id)
                .order_by(VoiceTranscriptSegment.sequence)
            )
        )
        if details
        else []
    )
    tools = (
        list(
            await session.scalars(
                select(VoiceToolInvocation)
                .where(VoiceToolInvocation.call_id == call.id)
                .order_by(VoiceToolInvocation.created_at)
            )
        )
        if details
        else []
    )
    review = await session.scalar(select(VoiceReview).where(VoiceReview.call_id == call.id)) if details else None
    recording = (
        await session.scalar(select(VoiceRecording).where(VoiceRecording.call_id == call.id)) if details else None
    )
    return VoiceCallRead(
        id=call.id,
        tenant_id=call.tenant_id,
        provider=call.provider,
        provider_call_id=call.provider_call_id,
        direction=call.direction,
        caller=call.caller,
        callee=call.callee,
        flow_key=call.flow_key,
        region=call.region,
        status=call.status,
        consent_status=call.consent_status,
        recording_status=call.recording_status,
        customer_id=call.customer_id,
        job_id=call.job_id,
        outcome=call.outcome,
        transfer_reason=call.transfer_reason,
        state=call.state,
        latency_metrics=call.latency_metrics,
        started_at=call.started_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        created_at=call.created_at,
        updated_at=call.updated_at,
        events=events,
        transcript=transcript,
        tools=tools,
        review=review,
        recording=recording,
    )


@router.get("/config", response_model=VoiceFlowConfigRead)
async def get_config(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> VoiceFlowConfig:
    config = await ensure_config(session, context.tenant_id)
    await session.commit()
    await session.refresh(config)
    return config


@router.put("/config", response_model=VoiceFlowConfigRead)
async def update_config(
    payload: VoiceFlowConfigUpdate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> VoiceFlowConfig:
    config = await ensure_config(session, context.tenant_id)
    for key, value in payload.model_dump().items():
        setattr(config, key, value)
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action="voice.flow_configured",
        resource_type="voice_flow",
        resource_id=config.id,
        correlation_id=request.state.correlation_id,
        payload={"enabled": config.enabled, "pilot_mode": config.pilot_mode, "prompt_version": config.prompt_version},
    )
    await session.commit()
    await session.refresh(config)
    return config


@router.get("/calls", response_model=list[VoiceCallRead])
async def calls(
    call_status: str | None = None,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[VoiceCallRead]:
    statement = select(VoiceCall).where(VoiceCall.tenant_id == context.tenant_id)
    if call_status:
        statement = statement.where(VoiceCall.status == call_status)
    records = list(await session.scalars(statement.order_by(VoiceCall.started_at.desc()).limit(200)))
    return [await call_read(session, call, details=False) for call in records]


@router.get("/calls/{call_id}", response_model=VoiceCallRead)
async def get_call(
    call_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> VoiceCallRead:
    return await call_read(session, await tenant_call(session, context.tenant_id, call_id))


@router.post("/calls/{call_id}/review", response_model=VoiceCallRead)
async def review_call(
    call_id: str,
    payload: VoiceReviewDecision,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher", "reviewer", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> VoiceCallRead:
    call = await tenant_call(session, context.tenant_id, call_id)
    review = await session.scalar(select(VoiceReview).where(VoiceReview.call_id == call.id))
    if review is None:
        raise HTTPException(status_code=404, detail="Call has no pending review")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="Call review has already been decided")
    review.reviewer_user_id = context.user.id
    review.reason = payload.reason
    review.decided_at = utcnow()
    if payload.decision == "reject":
        review.status = "rejected"
        call.status = "completed"
        call.outcome = "callback_rejected"
        await append_event(session, call, "review.rejected", {"reason": payload.reason})
    else:
        args = payload.args or review.proposed_args
        allowed = {"customer_id", "name", "phone", "reason", "title"}
        if set(args) - allowed:
            raise HTTPException(status_code=422, detail="Review contains unsupported fields")
        customer = None
        customer_created = False
        if args.get("customer_id"):
            customer = await session.scalar(
                select(Customer).where(
                    Customer.id == args["customer_id"],
                    Customer.tenant_id == context.tenant_id,
                )
            )
            if customer is None:
                raise HTTPException(status_code=422, detail="Reviewed customer does not exist")
        if customer is None:
            customer = Customer(
                tenant_id=context.tenant_id,
                name=str(args.get("name") or "Voice caller")[:200],
                phone=str(args.get("phone") or call.caller)[:50],
                notes="Created after reviewed after-hours intake",
            )
            session.add(customer)
            await session.flush()
            customer_created = True
        job = Job(
            tenant_id=context.tenant_id,
            customer_id=customer.id,
            title=str(args.get("title") or "Callback request")[:200],
            description=str(args.get("reason") or "Callback requested during voice intake"),
            status="draft",
        )
        session.add(job)
        await session.flush()
        session.add(
            JobStatusHistory(
                tenant_id=context.tenant_id,
                job_id=job.id,
                from_status=None,
                to_status="draft",
                occurred_at=utcnow(),
                actor_user_id=context.user.id,
            )
        )
        review.status = "approved"
        review.decided_args = args
        call.customer_id = customer.id
        call.job_id = job.id
        call.status = "completed"
        call.outcome = "callback_job_created"
        invocation = VoiceToolInvocation(
            tenant_id=context.tenant_id,
            call_id=call.id,
            name="create_callback_job",
            status="completed",
            input=args,
            output={"customer_id": customer.id, "job_id": job.id},
            latency_ms=0,
        )
        session.add(invocation)
        await append_event(
            session,
            call,
            "review.approved",
            {"action": "create_callback_job", "customer_id": customer.id, "job_id": job.id},
        )
        created_resources = [("job.created", "job", job.id)]
        if customer_created:
            created_resources.insert(0, ("customer.created", "customer", customer.id))
        for event_type, resource_type, resource_id in created_resources:
            session.add(
                OutboxEvent(
                    tenant_id=context.tenant_id,
                    event_type=event_type,
                    aggregate_type=resource_type,
                    aggregate_id=resource_id,
                    payload={"voice_call_id": call.id},
                    occurred_at=utcnow(),
                    correlation_id=request.state.correlation_id,
                )
            )
            session.add(
                AuditEvent(
                    tenant_id=context.tenant_id,
                    actor_user_id=context.user.id,
                    action=event_type,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    payload={"voice_call_id": call.id, "review_id": review.id},
                    occurred_at=utcnow(),
                    correlation_id=request.state.correlation_id,
                )
            )
    await session.commit()
    return await call_read(session, call)


@router.post("/calls/{call_id}/transfer", response_model=VoiceCallRead)
async def transfer_call(
    call_id: str,
    payload: VoiceTransferRequest,
    context: TenantContext = Depends(require_roles("owner", "dispatcher", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> VoiceCallRead:
    call = await tenant_call(session, context.tenant_id, call_id)
    call.status = "transferring"
    call.outcome = "human_transfer"
    call.transfer_reason = payload.reason
    await append_event(session, call, "transfer.requested", {"reason": payload.reason, "source": "operator"})
    await session.commit()
    return await call_read(session, call)


@router.get("/calls/{call_id}/recording")
async def download_recording(
    call_id: str,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "reviewer", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    call = await tenant_call(session, context.tenant_id, call_id)
    recording = await session.scalar(select(VoiceRecording).where(VoiceRecording.call_id == call.id))
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    content = AttachmentStorage(request.app.state.settings).get(recording.object_key)
    return Response(content=content, media_type=recording.content_type)


@router.post("/simulations", response_model=VoiceCallRead)
async def simulate_call(
    payload: VoiceSimulationCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> VoiceCallRead:
    config = await ensure_config(session, context.tenant_id)
    if not config.enabled:
        raise HTTPException(status_code=409, detail="Enable the voice pilot before running simulations")
    try:
        call, _ = await admit_call(
            session,
            tenant_id=context.tenant_id,
            provider="local-simulator",
            provider_call_id=f"sim-{uuid.uuid4()}",
            caller=payload.caller,
            callee=payload.callee,
            region=payload.region,
        )
    except AdmissionError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from None
    await answer_call(session, call)
    elapsed_ms = 0
    recording = bytearray()
    for turn in payload.turns:
        elapsed_ms += turn.duration_ms
        if turn.type == "transcript":
            text_value = turn.text or ""
            recording.extend(text_value.encode() + b"\n")
            await handle_user_turn(
                session,
                call,
                text_value,
                start_ms=max(0, elapsed_ms - turn.duration_ms),
                end_ms=elapsed_ms,
                confidence_bps=turn.confidence_bps,
            )
        elif turn.type == "dtmf":
            await handle_dtmf(session, call, turn.digit or "0", elapsed_ms)
        elif turn.type == "silence":
            await handle_silence(session, call)
        elif turn.type == "voicemail":
            await handle_voicemail(session, call)
        elif turn.type == "speech_start":
            await speech_started(session, call, turn.heard_response_boundary_ms)
        else:
            await hangup_call(session, call, "simulated_hangup")
            break
        if call.status in {"transferring", "failed"}:
            break
    await hangup_call(session, call, "simulation_complete")
    await store_recording(
        session,
        request.app.state.settings,
        call,
        bytes(recording),
        codec="simulation",
        sample_rate_hz=8000,
        duration_ms=elapsed_ms,
    )
    await session.commit()
    return await call_read(session, call)


@router.get("/metrics", response_model=VoiceMetricsRead)
async def voice_metrics(
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> VoiceMetricsRead:
    calls = list(await session.scalars(select(VoiceCall).where(VoiceCall.tenant_id == context.tenant_id)))
    total = len(calls)
    consented = sum(call.consent_status == "granted" for call in calls)
    transferred = sum(call.outcome == "human_transfer" for call in calls)
    first_audio = [
        value
        for call in calls
        for value in call.latency_metrics.get("speech_end_to_first_audio_ms", [])
        if isinstance(value, (int, float))
    ]
    average = round(sum(first_audio) / len(first_audio), 2) if first_audio else 0.0
    target = request.app.state.settings.voice_target_first_audio_ms
    return VoiceMetricsRead(
        total_calls=total,
        active_calls=sum(call.status in {"admitted", "in_progress", "transferring"} for call in calls),
        transferred=transferred,
        review_pending=sum(call.outcome == "callback_review_pending" for call in calls),
        completed=sum(call.status == "completed" for call in calls),
        consent_rate=round(consented / total, 4) if total else 0,
        transfer_rate=round(transferred / total, 4) if total else 0,
        average_first_audio_ms=average,
        target_first_audio_ms=target,
        within_latency_target=average <= target,
    )
