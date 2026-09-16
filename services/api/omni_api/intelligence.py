from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.audit import add_audit_event
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.call_pipeline import build_revision, process_call
from services.api.omni_api.database import get_session
from services.api.omni_api.intelligence_schemas import (
    CallFactRead,
    CallIntelligenceDetail,
    IntelligenceDashboard,
    IntelligenceRead,
    IntelligenceReview,
    IntelligenceSearchResult,
    MetricDefinition,
    PipelineRunRead,
    ReconciliationCreate,
    ReconciliationRead,
    TranscriptCorrection,
    TranscriptRevisionRead,
)
from services.api.omni_api.models import (
    CallFact,
    CallIntelligence,
    CallPipelineRun,
    CallReconciliation,
    CallTranscriptRevision,
    VoiceCall,
)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

METRICS = [
    MetricDefinition(
        key="volume", label="Call volume", definition="Modeled calls in the selected period.", unit="calls"
    ),
    MetricDefinition(
        key="answer_rate", label="Answer rate", definition="Answered calls divided by modeled calls.", unit="ratio"
    ),
    MetricDefinition(
        key="containment_rate",
        label="Containment rate",
        definition="Calls completed without human transfer or abandonment divided by modeled calls.",
        unit="ratio",
    ),
    MetricDefinition(
        key="transfer_rate", label="Transfer rate", definition="Human transfers divided by modeled calls.", unit="ratio"
    ),
    MetricDefinition(
        key="abandonment_rate",
        label="Abandonment rate",
        definition="Calls ending without a handled outcome divided by modeled calls.",
        unit="ratio",
    ),
    MetricDefinition(
        key="consent_rate", label="Consent rate", definition="Consented calls divided by modeled calls.", unit="ratio"
    ),
    MetricDefinition(
        key="first_audio_ms",
        label="First audio latency",
        definition="Mean speech-end-to-first-audio duration across modeled calls.",
        unit="milliseconds",
    ),
    MetricDefinition(
        key="estimated_cost",
        label="Estimated voice cost",
        definition="Deterministic pipeline estimate; replace with provider billing during reconciliation.",
        unit="micro-USD",
    ),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def tenant_call(session: AsyncSession, tenant_id: str, call_id: str) -> VoiceCall:
    call = await session.scalar(select(VoiceCall).where(VoiceCall.id == call_id, VoiceCall.tenant_id == tenant_id))
    if call is None:
        raise HTTPException(status_code=404, detail="Voice call not found")
    return call


async def latest_intelligence(session: AsyncSession, tenant_id: str, call_id: str) -> CallIntelligence:
    record = await session.scalar(
        select(CallIntelligence)
        .where(CallIntelligence.tenant_id == tenant_id, CallIntelligence.call_id == call_id)
        .order_by(CallIntelligence.version.desc())
        .limit(1)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Call has not been processed")
    return record


async def detail_read(session: AsyncSession, record: CallIntelligence) -> CallIntelligenceDetail:
    revision = await session.get(CallTranscriptRevision, record.transcript_revision_id)
    fact = await session.scalar(select(CallFact).where(CallFact.call_id == record.call_id))
    pipeline = await session.scalar(
        select(CallPipelineRun)
        .where(CallPipelineRun.call_id == record.call_id)
        .order_by(CallPipelineRun.created_at.desc())
        .limit(1)
    )
    reconciliations = list(
        await session.scalars(
            select(CallReconciliation)
            .where(CallReconciliation.call_id == record.call_id)
            .order_by(CallReconciliation.reconciled_at.desc())
        )
    )
    if revision is None or fact is None or pipeline is None:
        raise HTTPException(status_code=409, detail="Call processing is incomplete")
    return CallIntelligenceDetail(
        intelligence=IntelligenceRead.model_validate(record),
        transcript=TranscriptRevisionRead.model_validate(revision),
        fact=CallFactRead.model_validate(fact),
        pipeline=PipelineRunRead.model_validate(pipeline),
        reconciliations=[ReconciliationRead.model_validate(item) for item in reconciliations],
    )


@router.get("/metric-definitions", response_model=list[MetricDefinition])
async def metric_definitions(_: TenantContext = Depends(get_tenant_context)) -> list[MetricDefinition]:
    return METRICS


@router.post("/calls/{call_id}/process", response_model=CallIntelligenceDetail)
async def process_intelligence_call(
    call_id: str,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator", "reviewer")),
    session: AsyncSession = Depends(get_session),
) -> CallIntelligenceDetail:
    call = await tenant_call(session, context.tenant_id, call_id)
    if call.ended_at is None:
        raise HTTPException(status_code=409, detail="Call must end before processing")
    _, record = await process_call(session, call_id)
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action="call_intelligence.processed",
        resource_type="voice_call",
        resource_id=call_id,
        correlation_id=request.state.correlation_id,
        payload={"intelligence_id": record.id, "version": record.version},
    )
    await session.commit()
    return await detail_read(session, record)


@router.get("/calls", response_model=list[IntelligenceSearchResult])
async def search_calls(
    q: str | None = Query(default=None, max_length=200),
    topic: str | None = Query(default=None, max_length=100),
    review_status: str | None = Query(default=None, max_length=30),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[IntelligenceSearchResult]:
    statement = (
        select(CallIntelligence, VoiceCall)
        .join(VoiceCall, VoiceCall.id == CallIntelligence.call_id)
        .where(CallIntelligence.tenant_id == context.tenant_id)
    )
    if q:
        pattern = f"%{q}%"
        statement = statement.where(
            or_(CallIntelligence.search_text.ilike(pattern), CallIntelligence.summary.ilike(pattern))
        )
    if topic:
        statement = statement.where(CallIntelligence.topic == topic)
    if review_status:
        statement = statement.where(CallIntelligence.status == review_status)
    rows = (await session.execute(statement.order_by(CallIntelligence.created_at.desc()).limit(500))).all()
    seen: set[str] = set()
    results = []
    for intelligence, call in rows:
        if call.id in seen:
            continue
        seen.add(call.id)
        source = intelligence.search_text
        index = source.lower().find(q.lower()) if q else 0
        snippet = source[max(0, index - 80) : index + 180] if source else intelligence.summary
        results.append(
            IntelligenceSearchResult(
                call_id=call.id,
                topic=intelligence.topic,
                intent=intelligence.intent,
                summary=intelligence.summary,
                outcome=intelligence.outcome,
                confidence_bps=intelligence.confidence_bps,
                status=intelligence.status,
                started_at=call.started_at,
                snippet=snippet,
            )
        )
    return results


@router.get("/calls/{call_id}", response_model=CallIntelligenceDetail)
async def intelligence_call(
    call_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> CallIntelligenceDetail:
    await tenant_call(session, context.tenant_id, call_id)
    return await detail_read(session, await latest_intelligence(session, context.tenant_id, call_id))


@router.post("/calls/{call_id}/transcript-corrections", response_model=CallIntelligenceDetail)
async def correct_transcript(
    call_id: str,
    payload: TranscriptCorrection,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator", "reviewer")),
    session: AsyncSession = Depends(get_session),
) -> CallIntelligenceDetail:
    call = await tenant_call(session, context.tenant_id, call_id)
    prior = await session.scalar(
        select(CallTranscriptRevision)
        .where(CallTranscriptRevision.call_id == call.id)
        .order_by(CallTranscriptRevision.version.desc())
        .limit(1)
    )
    revision = await build_revision(
        session,
        call,
        corrected_segments=[item.model_dump() for item in payload.segments],
        corrected_by_user_id=context.user.id,
        correction_reason=payload.reason,
        source_revision_id=prior.id if prior else None,
    )
    _, record = await process_call(session, call.id, transcript_revision_id=revision.id)
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action="call_transcript.corrected",
        resource_type="voice_call",
        resource_id=call.id,
        correlation_id=request.state.correlation_id,
        payload={"revision_id": revision.id, "version": revision.version, "reason": payload.reason},
    )
    await session.commit()
    return await detail_read(session, record)


@router.post("/calls/{call_id}/review", response_model=IntelligenceRead)
async def review_intelligence(
    call_id: str,
    payload: IntelligenceReview,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "administrator", "reviewer")),
    session: AsyncSession = Depends(get_session),
) -> CallIntelligence:
    await tenant_call(session, context.tenant_id, call_id)
    record = await latest_intelligence(session, context.tenant_id, call_id)
    if record.status not in {"pending_review", "accepted"} or record.reviewer_user_id is not None:
        raise HTTPException(status_code=409, detail="Intelligence review has already been decided")
    if payload.decision == "correct" and not payload.corrected_fields:
        raise HTTPException(status_code=422, detail="Corrected fields are required")
    if payload.corrected_fields:
        corrections = payload.corrected_fields.model_dump(exclude_none=True)
        for key, value in corrections.items():
            setattr(record, key, value)
    else:
        corrections = None
    record.status = {"accept": "accepted", "correct": "corrected", "reject": "rejected"}[payload.decision]
    record.corrected_fields = corrections
    record.reviewer_user_id = context.user.id
    record.review_reason = payload.reason
    record.reviewed_at = utcnow()
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action=f"call_intelligence.{record.status}",
        resource_type="call_intelligence",
        resource_id=record.id,
        correlation_id=request.state.correlation_id,
        payload={"call_id": call_id, "reason": payload.reason},
    )
    await session.commit()
    await session.refresh(record)
    return record


@router.post("/calls/{call_id}/reconcile", response_model=ReconciliationRead)
async def reconcile_call(
    call_id: str,
    payload: ReconciliationCreate,
    context: TenantContext = Depends(require_roles("owner", "administrator")),
    session: AsyncSession = Depends(get_session),
) -> CallReconciliation:
    call = await tenant_call(session, context.tenant_id, call_id)
    fact = await session.scalar(select(CallFact).where(CallFact.call_id == call.id))
    if fact is None:
        raise HTTPException(status_code=409, detail="Process the call before reconciliation")
    existing = await session.scalar(
        select(CallReconciliation).where(
            CallReconciliation.call_id == call.id,
            CallReconciliation.provider_snapshot_id == payload.provider_snapshot_id,
        )
    )
    discrepancies = []
    if payload.provider_status != call.status:
        discrepancies.append("status_mismatch")
    if abs(payload.provider_duration_ms - fact.duration_ms) > 1000:
        discrepancies.append("duration_mismatch")
    values = {
        "provider_status": payload.provider_status,
        "provider_duration_ms": payload.provider_duration_ms,
        "internal_status": call.status,
        "internal_duration_ms": fact.duration_ms,
        "complete": not discrepancies,
        "discrepancies": discrepancies,
        "reconciled_at": utcnow(),
    }
    if existing is None:
        existing = CallReconciliation(
            tenant_id=context.tenant_id,
            call_id=call.id,
            provider_snapshot_id=payload.provider_snapshot_id,
            **values,
        )
        session.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    await session.commit()
    await session.refresh(existing)
    return existing


@router.get("/dashboard", response_model=IntelligenceDashboard)
async def dashboard(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> IntelligenceDashboard:
    facts = list(await session.scalars(select(CallFact).where(CallFact.tenant_id == context.tenant_id)))
    intelligence = list(
        await session.scalars(
            select(CallIntelligence)
            .where(CallIntelligence.tenant_id == context.tenant_id)
            .order_by(CallIntelligence.version.desc())
        )
    )
    latest = {}
    for item in intelligence:
        latest.setdefault(item.call_id, item)
    records = list(latest.values())
    reconciliations = list(
        await session.scalars(select(CallReconciliation).where(CallReconciliation.tenant_id == context.tenant_id))
    )
    pipelines = list(
        await session.scalars(select(CallPipelineRun).where(CallPipelineRun.tenant_id == context.tenant_id))
    )
    total = len(facts)

    def ratio(value: int) -> float:
        return round(value / total, 4) if total else 0.0

    def buckets(values: list[str]) -> list[dict]:
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return [{"key": key, "count": count} for key, count in sorted(counts.items(), key=lambda item: -item[1])]

    sentiment_values = [
        item.sentiment_trajectory[-1].get("label", "unknown") if item.sentiment_trajectory else "unknown"
        for item in records
    ]
    freshness = max((item.updated_at for item in facts), default=None)
    return IntelligenceDashboard(
        total_calls=total,
        answered_rate=ratio(sum(item.answered for item in facts)),
        containment_rate=ratio(sum(item.contained for item in facts)),
        transfer_rate=ratio(sum(item.transferred for item in facts)),
        abandonment_rate=ratio(sum(item.abandoned for item in facts)),
        consent_rate=ratio(sum(item.consented for item in facts)),
        average_duration_ms=round(sum(item.duration_ms for item in facts) / total, 2) if total else 0,
        average_first_audio_ms=round(sum(item.first_audio_ms for item in facts) / total, 2) if total else 0,
        estimated_cost_microusd=sum(item.estimated_cost_microusd for item in facts),
        review_pending=sum(item.status == "pending_review" for item in records),
        compliance_flagged=sum(bool(item.compliance_flags) for item in records),
        reconciliation_rate=ratio(
            sum(item.complete for item in reconciliations if item.provider_snapshot_id.startswith("internal:"))
        ),
        pipeline_success_rate=(
            round(sum(item.status == "completed" for item in pipelines) / len(pipelines), 4) if pipelines else 0
        ),
        data_freshness_at=freshness,
        topics=buckets([item.topic for item in records]),
        outcomes=buckets([item.outcome for item in records]),
        sentiment=buckets(sentiment_values),
    )
