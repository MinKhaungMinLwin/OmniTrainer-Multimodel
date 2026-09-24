import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from openinference.semconv.trace import OpenInferenceSpanKindValues
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.ai_gateway import plan_with_resilience
from services.api.omni_api.ai_schemas import (
    ApprovalDecision,
    ApprovalRead,
    AudioTranscriptionRead,
    ConversationCreate,
    ConversationRead,
    EventRead,
    ExtractionCreate,
    ExtractionRead,
    ExtractionReview,
    FeedbackCreate,
    FeedbackRead,
    KnowledgeDocumentCreate,
    KnowledgeDocumentRead,
    KnowledgeSearchResult,
    MessageRead,
    RunCreate,
    RunDetail,
    RunRead,
    SpeechCreate,
    ToolInvocationRead,
)
from services.api.omni_api.ai_tools import (
    TOOL_REGISTRY,
    ToolInputError,
    execute_tool,
    normalize_tool_arguments,
    registry_payload,
    search_knowledge,
)
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.database import get_session
from services.api.omni_api.gemini_audio import (
    SUPPORTED_AUDIO_TYPES,
    GeminiAudioError,
    GeminiAudioService,
    GeminiConfigurationError,
)
from services.api.omni_api.models import (
    AgentEvent,
    AgentFeedback,
    AgentRun,
    ApprovalRequest,
    Conversation,
    ConversationMessage,
    ExtractionRun,
    KnowledgeChunk,
    KnowledgeDocument,
    ToolInvocation,
)
from services.api.omni_api.operations import record_change
from services.observability import current_trace_id, hash_identifier, tool_argument_summary, traced_span

router = APIRouter(prefix="/ai", tags=["ai-copilot"])


@router.post("/audio/transcriptions", response_model=AudioTranscriptionRead)
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    context: TenantContext = Depends(get_tenant_context),
) -> AudioTranscriptionRead:
    del context
    mime_type = (file.content_type or "").split(";", 1)[0].lower()
    if mime_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported audio format")
    limit = request.app.state.settings.ai_max_audio_bytes
    audio = await file.read(limit + 1)
    if not audio:
        raise HTTPException(status_code=422, detail="The recording is empty")
    if len(audio) > limit:
        raise HTTPException(status_code=413, detail="The recording is too large")
    try:
        result = await GeminiAudioService(request.app.state.settings).transcribe(audio, mime_type)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except GeminiAudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return AudioTranscriptionRead(text=result.text, model=result.model)


@router.post("/audio/speech", responses={200: {"content": {"audio/wav": {}}}})
async def synthesize_speech(
    payload: SpeechCreate,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
) -> Response:
    del context
    try:
        audio = await GeminiAudioService(request.app.state.settings).synthesize(payload.text, payload.voice)
    except GeminiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except GeminiAudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(
        audio,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def tenant_conversation(session: AsyncSession, tenant_id: str, conversation_id: str) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


async def tenant_run(session: AsyncSession, tenant_id: str, run_id: str, *, lock: bool = False) -> AgentRun:
    query = select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    run = await session.scalar(query)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def append_event(
    session: AsyncSession, run: AgentRun, event_type: str, payload: dict[str, Any] | None = None
) -> AgentEvent:
    run.last_sequence += 1
    event = AgentEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=run.last_sequence,
        event_type=event_type,
        payload=payload or {},
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(event)
    await session.flush()
    return event


async def run_detail(session: AsyncSession, run: AgentRun) -> RunDetail:
    events = list(
        await session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence))
    )
    tools = list(
        await session.scalars(
            select(ToolInvocation).where(ToolInvocation.run_id == run.id).order_by(ToolInvocation.created_at)
        )
    )
    approvals = list(
        await session.scalars(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run.id).order_by(ApprovalRequest.created_at)
        )
    )
    return RunDetail(
        run=RunRead.model_validate(run),
        events=[EventRead.model_validate(event) for event in events],
        tools=[ToolInvocationRead.model_validate(tool) for tool in tools],
        approvals=[ApprovalRead.model_validate(approval) for approval in approvals],
    )


def text_chunks(value: str, size: int = 44) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)]


async def finish_run(
    session: AsyncSession,
    run: AgentRun,
    content: str,
    *,
    citations: list[dict[str, Any]] | None = None,
    parts: list[dict[str, Any]] | None = None,
    corrected: bool = False,
) -> ConversationMessage:
    for chunk in text_chunks(content):
        await append_event(session, run, "text_delta", {"delta": chunk})
    message = ConversationMessage(
        tenant_id=run.tenant_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        role="assistant",
        content=content,
        parts=parts or [],
        citations=citations or [],
    )
    session.add(message)
    run.output_tokens += max(1, len(content.split()))
    run.status = "completed"
    await session.flush()
    if corrected:
        await append_event(session, run, "corrected_result", {"message_id": message.id})
    await append_event(session, run, "completed", {"message_id": message.id})
    return message


async def execute_read_plan(
    session: AsyncSession,
    run: AgentRun,
    tool: ToolInvocation,
    context: TenantContext,
    request: Request,
) -> dict[str, Any] | None:
    tool.status = "running"
    tool.started_at = datetime.now(timezone.utc)
    await append_event(
        session,
        run,
        "tool_running",
        {"tool_invocation_id": tool.id, "name": tool.name},
    )
    try:
        with traced_span(
            f"tool.{tool.name}",
            OpenInferenceSpanKindValues.TOOL,
            session_id=run.conversation_id,
            user_id=context.user.id,
            metadata={"tenant_hash": hash_identifier(context.tenant_id), "run_id": run.id},
            attributes={
                "tool.name": tool.name,
                "tool.risk": tool.risk,
                "tool.input.summary": tool_argument_summary(tool.input),
                "omni.human_reviewed": tool.risk != "read",
            },
        ) as span:
            result = await asyncio.wait_for(
                execute_tool(
                    tool.name,
                    tool.input,
                    session=session,
                    context=context,
                    request=request,
                    idempotency_key=tool.idempotency_key,
                ),
                timeout=TOOL_REGISTRY[tool.name].timeout_seconds,
            )
            span.set_attribute("tool.result.status", "completed")
    except (HTTPException, ToolInputError, TimeoutError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        tool.status = "failed"
        tool.error = {"code": "tool_error", "message": detail}
        tool.completed_at = datetime.now(timezone.utc)
        await append_event(
            session,
            run,
            "tool_result",
            {"tool_invocation_id": tool.id, "status": "failed", "error": tool.error},
        )
        return None
    tool.status = "completed"
    tool.output = result
    tool.completed_at = datetime.now(timezone.utc)
    await append_event(
        session,
        run,
        "tool_result",
        {"tool_invocation_id": tool.id, "status": "completed", "result": result},
    )
    return result


async def process_new_run(
    session: AsyncSession,
    run: AgentRun,
    prompt: str,
    context: TenantContext,
    request: Request,
) -> None:
    with traced_span(
        "copilot.agent.run",
        OpenInferenceSpanKindValues.AGENT,
        session_id=run.conversation_id,
        user_id=context.user.id,
        metadata={
            "tenant_hash": hash_identifier(context.tenant_id),
            "run_id": run.id,
            "correlation_id": request.state.correlation_id,
        },
        attributes={
            "agent.provider": run.provider,
            "agent.model": run.model,
            "agent.prompt_version": run.prompt_version,
            "agent.toolset_version": run.toolset_version,
        },
    ) as span:
        await _process_new_run(session, run, prompt, context, request)
        span.set_attribute("agent.status", run.status)
        span.set_attribute("agent.input_tokens", run.input_tokens)
        span.set_attribute("agent.output_tokens", run.output_tokens)
        span.set_attribute("agent.cost_micros", run.cost_micros)


async def _process_new_run(
    session: AsyncSession,
    run: AgentRun,
    prompt: str,
    context: TenantContext,
    request: Request,
) -> None:
    run.status = "running"
    trace_id = current_trace_id()
    await append_event(
        session,
        run,
        "run_started",
        {
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "toolset_version": run.toolset_version,
            "trace_id": trace_id,
            "trace_url": request.app.state.settings.tracing_public_url if trace_id else None,
        },
    )
    provider_prompt = prompt
    if run.provider == "gemini":
        recent_messages = list(
            await session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == run.conversation_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(12)
            )
        )
        recent_messages.reverse()
        provider_prompt = "Conversation history:\n" + "\n".join(
            f"{message.role.upper()}: {message.content}" for message in recent_messages
        )
    plan = await plan_with_resilience(
        run.provider,
        provider_prompt,
        timeout_seconds=request.app.state.settings.ai_timeout_seconds,
        api_key=request.app.state.settings.gemini_api_key,
        model=request.app.state.settings.ai_model,
        base_url=request.app.state.settings.gemini_base_url,
    )
    run.provider = plan.provider
    run.model = plan.model
    run.input_tokens = plan.input_tokens
    run.cost_micros = plan.cost_micros
    summaries: list[str] = []
    citations: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for plan_item in plan.tools:
        definition = TOOL_REGISTRY.get(plan_item.name)
        if definition is None or context.role not in definition.roles:
            run.status = "failed"
            run.error_code = "tool_not_allowed"
            run.error_message = "The requested tool is unavailable for this role."
            await append_event(session, run, "failed", {"code": run.error_code, "message": run.error_message})
            return
        tool = ToolInvocation(
            tenant_id=context.tenant_id,
            run_id=run.id,
            name=definition.name,
            version=definition.version,
            risk=definition.risk,
            status="proposed",
            input=normalize_tool_arguments(definition, plan_item.arguments),
            idempotency_key=f"ai-tool-{run.id}-{len(parts) + 1}",
        )
        session.add(tool)
        await session.flush()
        await append_event(
            session,
            run,
            "tool_proposed",
            {
                "tool_invocation_id": tool.id,
                "name": tool.name,
                "title": definition.title,
                "risk": tool.risk,
                "arguments": tool.input,
            },
        )
        parts.append({"type": "tool", "tool_invocation_id": tool.id})
        if definition.risk != "read":
            approval = ApprovalRequest(
                tenant_id=context.tenant_id,
                run_id=run.id,
                tool_invocation_id=tool.id,
                status="pending",
                proposed_args=tool.input,
            )
            session.add(approval)
            tool.status = "waiting_approval"
            run.status = "waiting_approval"
            await session.flush()
            await append_event(
                session,
                run,
                "approval_requested",
                {
                    "approval_id": approval.id,
                    "tool_invocation_id": tool.id,
                    "name": tool.name,
                    "risk": tool.risk,
                    "arguments": tool.input,
                },
            )
            record_change(
                session,
                context=context,
                request=request,
                event_type="ai.approval_requested",
                resource_type="agent_run",
                resource_id=run.id,
                payload={"approval_id": approval.id, "tool": tool.name, "risk": tool.risk},
            )
            return
        result = await execute_read_plan(session, run, tool, context, request)
        if result is None:
            summaries.append(f"{definition.title} could not complete. You can retry this request.")
        else:
            summaries.append(result["summary"])
            citations.extend(result.get("citations", []))
    await finish_run(
        session,
        run,
        " ".join(summaries) or plan.introduction or "I could not find a result.",
        citations=citations,
        parts=parts,
    )


@router.get("/tools")
async def list_tools(context: TenantContext = Depends(get_tenant_context)) -> list[dict[str, Any]]:
    return registry_payload(context.role)


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conversation = Conversation(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        title=payload.title,
    )
    session.add(conversation)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="conversation.created",
        resource_type="conversation",
        resource_id=conversation.id,
    )
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[Conversation]:
    return list(
        await session.scalars(
            select(Conversation)
            .where(Conversation.tenant_id == context.tenant_id)
            .order_by(Conversation.updated_at.desc())
        )
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationMessage]:
    await tenant_conversation(session, context.tenant_id, conversation_id)
    return list(
        await session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at)
        )
    )


@router.post("/conversations/{conversation_id}/runs", response_model=RunDetail, status_code=201)
async def create_run(
    conversation_id: str,
    payload: RunCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    conversation = await tenant_conversation(session, context.tenant_id, conversation_id)
    prior = await session.scalar(
        select(AgentRun).where(
            AgentRun.tenant_id == context.tenant_id,
            AgentRun.idempotency_key == idempotency_key,
        )
    )
    if prior:
        return await run_detail(session, prior)
    recent = await session.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.tenant_id == context.tenant_id,
            AgentRun.created_at >= datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    limit = request.app.state.settings.ai_runs_per_minute
    if (recent or 0) >= limit:
        raise HTTPException(status_code=429, detail="AI run rate limit reached; retry shortly")
    if payload.parent_run_id:
        await tenant_run(session, context.tenant_id, payload.parent_run_id)
    user_message = ConversationMessage(
        tenant_id=context.tenant_id,
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        parts=[],
        citations=[],
    )
    session.add(user_message)
    await session.flush()
    run = AgentRun(
        tenant_id=context.tenant_id,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        parent_run_id=payload.parent_run_id,
        idempotency_key=idempotency_key,
        provider=request.app.state.settings.ai_provider,
        model=request.app.state.settings.ai_model,
    )
    session.add(run)
    await session.flush()
    user_message.run_id = run.id
    if conversation.title == "New conversation":
        conversation.title = payload.content[:80]
    record_change(
        session,
        context=context,
        request=request,
        event_type="ai.run_started",
        resource_type="agent_run",
        resource_id=run.id,
        payload={"conversation_id": conversation.id},
    )
    try:
        await process_new_run(session, run, payload.content, context, request)
    except Exception as exc:
        run.status = "failed"
        run.error_code = "internal_error"
        run.error_message = "The run failed safely. Retry with the same context."
        await append_event(
            session,
            run,
            "failed",
            {"code": run.error_code, "message": run.error_message},
        )
        request.app.state.last_ai_error = repr(exc)
    await session.commit()
    return await run_detail(session, run)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    return await run_detail(session, await tenant_run(session, context.tenant_id, run_id))


@router.get("/runs/{run_id}/events", response_model=list[EventRead])
async def list_run_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[AgentEvent]:
    await tenant_run(session, context.tenant_id, run_id)
    return list(
        await session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id, AgentEvent.sequence > after)
            .order_by(AgentEvent.sequence)
        )
    )


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await tenant_run(session, context.tenant_id, run_id)
    cursor = max(after, int(last_event_id or 0))
    events = list(
        await session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id, AgentEvent.sequence > cursor)
            .order_by(AgentEvent.sequence)
        )
    )

    async def generate():
        for event in events:
            if await request.is_disconnected():
                break
            payload = EventRead.model_validate(event).model_dump(mode="json")
            yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.01)
        yield ": stream-complete\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(
    run_id: str,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    run = await tenant_run(session, context.tenant_id, run_id, lock=True)
    if run.status not in {"completed", "failed", "cancelled"}:
        run.cancel_requested = True
        run.status = "cancelled"
        await append_event(session, run, "cancelled", {"reason": "user_requested"})
        record_change(
            session,
            context=context,
            request=request,
            event_type="ai.run_cancelled",
            resource_type="agent_run",
            resource_id=run.id,
        )
        await session.commit()
    return await run_detail(session, run)


@router.post("/runs/{run_id}/regenerate", response_model=RunDetail, status_code=201)
async def regenerate_run(
    run_id: str,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    original = await tenant_run(session, context.tenant_id, run_id)
    message = await session.scalar(
        select(ConversationMessage).where(ConversationMessage.id == original.user_message_id)
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Original message not found")
    return await create_run(
        original.conversation_id,
        RunCreate(content=message.content, parent_run_id=original.id),
        request,
        idempotency_key,
        context,
        session,
    )


@router.post("/approvals/{approval_id}/decision", response_model=RunDetail)
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    approval = await session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    run = await tenant_run(session, context.tenant_id, approval.run_id, lock=True)
    tool = await session.scalar(
        select(ToolInvocation).where(
            ToolInvocation.id == approval.tool_invocation_id,
            ToolInvocation.tenant_id == context.tenant_id,
        )
    )
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool invocation not found")
    definition = TOOL_REGISTRY[tool.name]
    if context.role not in definition.roles:
        raise HTTPException(status_code=403, detail="Role does not permit this approval")
    if approval.status != "pending":
        return await run_detail(session, run)

    approval.reviewer_user_id = context.user.id
    approval.decided_at = datetime.now(timezone.utc)
    approval.reason = payload.reason
    if payload.decision == "reject":
        approval.status = "rejected"
        approval.decided_args = approval.proposed_args
        tool.status = "rejected"
        run.status = "running"
        with traced_span(
            "copilot.approval.decision",
            OpenInferenceSpanKindValues.CHAIN,
            session_id=run.conversation_id,
            user_id=context.user.id,
            metadata={"tenant_hash": hash_identifier(context.tenant_id), "run_id": run.id},
            attributes={
                "approval.decision": "reject",
                "approval.tool_name": tool.name,
                "approval.edited": False,
            },
        ):
            pass
        await append_event(
            session,
            run,
            "tool_result",
            {
                "tool_invocation_id": tool.id,
                "status": "rejected",
                "reason": payload.reason,
            },
        )
        await finish_run(
            session,
            run,
            f"The proposed {definition.title.lower()} was rejected and no change was made.",
            parts=[{"type": "tool", "tool_invocation_id": tool.id}],
        )
    else:
        if payload.decision == "edit" and payload.arguments is None:
            raise HTTPException(status_code=422, detail="Edited arguments are required")
        arguments = payload.arguments if payload.decision == "edit" else approval.proposed_args
        approval.status = "edited" if payload.decision == "edit" else "approved"
        approval.decided_args = arguments
        tool.input = arguments
        run.status = "running"
        with traced_span(
            "copilot.approval.decision",
            OpenInferenceSpanKindValues.CHAIN,
            session_id=run.conversation_id,
            user_id=context.user.id,
            metadata={"tenant_hash": hash_identifier(context.tenant_id), "run_id": run.id},
            attributes={
                "approval.decision": payload.decision,
                "approval.tool_name": tool.name,
                "approval.edited": payload.decision == "edit",
            },
        ) as approval_span:
            result = await execute_read_plan(session, run, tool, context, request)
            approval_span.set_attribute("approval.execution_status", tool.status)
        if result is None:
            run.status = "failed"
            run.error_code = "tool_error"
            run.error_message = "The approved action failed safely; no further actions ran."
            await append_event(
                session,
                run,
                "failed",
                {"code": run.error_code, "message": run.error_message},
            )
        else:
            await finish_run(
                session,
                run,
                result["summary"],
                citations=result.get("citations", []),
                parts=[{"type": "tool", "tool_invocation_id": tool.id}],
                corrected=payload.decision == "edit",
            )
    record_change(
        session,
        context=context,
        request=request,
        event_type=f"ai.approval_{approval.status}",
        resource_type="agent_run",
        resource_id=run.id,
        payload={"approval_id": approval.id, "tool": tool.name, "reason": payload.reason},
    )
    await session.commit()
    return await run_detail(session, run)


@router.post("/runs/{run_id}/feedback", response_model=FeedbackRead, status_code=201)
async def add_feedback(
    run_id: str,
    payload: FeedbackCreate,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> AgentFeedback:
    run = await tenant_run(session, context.tenant_id, run_id)
    if payload.message_id:
        message = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.id == payload.message_id,
                ConversationMessage.run_id == run.id,
            )
        )
        if message is None:
            raise HTTPException(status_code=404, detail="Run message not found")
    feedback = AgentFeedback(
        tenant_id=context.tenant_id,
        run_id=run.id,
        message_id=payload.message_id,
        user_id=context.user.id,
        rating=payload.rating,
        category=payload.category,
        comment=payload.comment,
        run_context={
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "toolset_version": run.toolset_version,
            "policy_version": run.policy_version,
            "knowledge_version": run.knowledge_version,
        },
    )
    session.add(feedback)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="ai.feedback_added",
        resource_type="agent_run",
        resource_id=run.id,
        payload={"rating": feedback.rating, "category": feedback.category},
    )
    await session.commit()
    await session.refresh(feedback)
    return feedback


def chunks(content: str, size: int = 800) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
    output: list[str] = []
    for paragraph in paragraphs:
        output.extend(paragraph[index : index + size] for index in range(0, len(paragraph), size))
    return output


@router.post("/knowledge", response_model=KnowledgeDocumentRead, status_code=201)
async def ingest_knowledge(
    payload: KnowledgeDocumentCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocument:
    allowed_roles = {"owner", "dispatcher", "technician", "accountant", "reviewer", "administrator"}
    if not set(payload.access_roles).issubset(allowed_roles):
        raise HTTPException(status_code=422, detail="Unknown knowledge access role")
    content_hash = hashlib.sha256(payload.content.encode()).hexdigest()
    duplicate = await session.scalar(
        select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == context.tenant_id,
            KnowledgeDocument.content_hash == content_hash,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="This knowledge content is already indexed")
    document = KnowledgeDocument(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        title=payload.title,
        source_uri=payload.source_uri,
        content=payload.content,
        content_hash=content_hash,
        status="indexed",
        access_roles=payload.access_roles,
    )
    session.add(document)
    await session.flush()
    for ordinal, content in enumerate(chunks(payload.content)):
        session.add(
            KnowledgeChunk(
                tenant_id=context.tenant_id,
                document_id=document.id,
                ordinal=ordinal,
                content=content,
                token_count=max(1, len(content.split())),
                chunk_metadata={"title": document.title, "untrusted_content": True},
            )
        )
    record_change(
        session,
        context=context,
        request=request,
        event_type="knowledge.indexed",
        resource_type="knowledge_document",
        resource_id=document.id,
        payload={"title": document.title, "content_hash": content_hash},
    )
    await session.commit()
    await session.refresh(document)
    return document


@router.get("/knowledge", response_model=list[KnowledgeDocumentRead])
async def list_knowledge(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDocument]:
    documents = await session.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == context.tenant_id)
        .order_by(KnowledgeDocument.updated_at.desc())
    )
    return [item for item in documents if not item.access_roles or context.role in item.access_roles]


@router.get("/knowledge/search", response_model=list[KnowledgeSearchResult])
async def knowledge_search(
    query: str = Query(min_length=1, max_length=1000),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeSearchResult]:
    return [
        KnowledgeSearchResult(**item)
        for item in await search_knowledge(session, context.tenant_id, context.role, query)
    ]


@router.delete("/knowledge/{document_id}", status_code=204)
async def remove_knowledge(
    document_id: str,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == context.tenant_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id == document.id))
    record_change(
        session,
        context=context,
        request=request,
        event_type="knowledge.deleted",
        resource_type="knowledge_document",
        resource_id=document.id,
    )
    await session.commit()
    return Response(status_code=204)


def extract_fields(schema_name: str, text: str) -> tuple[dict[str, Any], int, dict[str, Any]]:
    email = re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text)
    phone = re.search(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)", text)
    fields: dict[str, Any] = {}
    offsets: dict[str, list[int]] = {}
    if email:
        fields["email"] = email.group(0)
        offsets["email"] = [email.start(), email.end()]
    if phone:
        fields["phone"] = phone.group(0)
        offsets["phone"] = [phone.start(), phone.end()]
    if schema_name in {"lead_intake", "contact"}:
        name = re.search(r"(?:name is|this is|contact)\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)", text)
        if name:
            fields["name"] = name.group(1)
            offsets["name"] = [name.start(1), name.end(1)]
    if schema_name in {"lead_intake", "job_request"}:
        fields["summary"] = text[:500]
        offsets["summary"] = [0, min(len(text), 500)]
        urgency = "urgent" if re.search(r"\b(urgent|emergency|asap)\b", text, re.IGNORECASE) else "normal"
        fields["urgency"] = urgency
    confidence = min(9800, 4500 + len(offsets) * 1500)
    return fields, confidence, {"extractor": "rules-v1", "source_offsets": offsets}


@router.post("/extractions", response_model=ExtractionRead, status_code=201)
async def create_extraction(
    payload: ExtractionCreate,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> ExtractionRun:
    fields, confidence, provenance = extract_fields(payload.schema_name, payload.input_text)
    extraction = ExtractionRun(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        schema_name=payload.schema_name,
        schema_version="1",
        input_text=payload.input_text,
        extracted_fields=fields,
        confidence_bps=confidence,
        provenance=provenance,
        status="accepted" if confidence >= 8000 else "pending_review",
    )
    session.add(extraction)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="extraction.created",
        resource_type="extraction",
        resource_id=extraction.id,
        payload={"schema": extraction.schema_name, "confidence_bps": confidence},
    )
    await session.commit()
    await session.refresh(extraction)
    return extraction


@router.get("/extractions", response_model=list[ExtractionRead])
async def list_extractions(
    review_status: str | None = Query(default=None, alias="status"),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[ExtractionRun]:
    filters = [ExtractionRun.tenant_id == context.tenant_id]
    if review_status:
        filters.append(ExtractionRun.status == review_status)
    return list(await session.scalars(select(ExtractionRun).where(*filters).order_by(ExtractionRun.created_at.desc())))


@router.post("/extractions/{extraction_id}/review", response_model=ExtractionRead)
async def review_extraction(
    extraction_id: str,
    payload: ExtractionReview,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "reviewer", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> ExtractionRun:
    extraction = await session.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.id == extraction_id, ExtractionRun.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if extraction is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    if payload.decision == "correct" and payload.corrected_fields is None:
        raise HTTPException(status_code=422, detail="Corrected fields are required")
    extraction.status = {"accept": "accepted", "correct": "corrected", "reject": "rejected"}[payload.decision]
    extraction.corrected_fields = payload.corrected_fields
    extraction.reviewer_user_id = context.user.id
    extraction.review_reason = payload.reason
    extraction.reviewed_at = datetime.now(timezone.utc)
    record_change(
        session,
        context=context,
        request=request,
        event_type=f"extraction.{extraction.status}",
        resource_type="extraction",
        resource_id=extraction.id,
        payload={"reason": payload.reason},
    )
    await session.commit()
    await session.refresh(extraction)
    return extraction
    AudioTranscriptionRead,
