import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.models import (
    CallFact,
    CallIntelligence,
    CallPipelineRun,
    CallReconciliation,
    CallTranscriptRevision,
    VoiceCall,
    VoiceCallEvent,
    VoiceToolInvocation,
    VoiceTranscriptSegment,
)

PROCESSOR_VERSION = "transcript-clean-v1"
EXTRACTOR_VERSION = "conversation-intelligence-v1"
PIPELINE_VERSION = "call-data-v1"

PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def redact(text: str) -> str:
    return PHONE_PATTERN.sub("[PHONE]", EMAIL_PATTERN.sub("[EMAIL]", text))


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def classify_topic(text: str) -> str:
    lowered = text.lower()
    groups = {
        "safety_emergency": ("fire", "gas leak", "emergency", "danger"),
        "heating_cooling": ("heating", "heater", "furnace", "air conditioning", "hvac"),
        "plumbing": ("leak", "sink", "pipe", "toilet", "water"),
        "appointment": ("appointment", "schedule", "reschedule"),
        "billing": ("invoice", "bill", "payment", "charge"),
    }
    return next((topic for topic, terms in groups.items() if any(term in lowered for term in terms)), "general_service")


def sentiment(text: str) -> list[dict]:
    lowered = text.lower()
    negative = sum(lowered.count(term) for term in ("stopped", "broken", "leak", "urgent", "angry", "problem"))
    positive = sum(lowered.count(term) for term in ("thank", "yes", "great", "helpful"))
    label = "negative" if negative > positive else "positive" if positive > negative else "neutral"
    return [{"position": "overall", "label": label, "score_bps": min(9500, 6000 + abs(negative - positive) * 500)}]


async def build_revision(
    session: AsyncSession,
    call: VoiceCall,
    *,
    corrected_segments: list[dict] | None = None,
    corrected_by_user_id: str | None = None,
    correction_reason: str | None = None,
    source_revision_id: str | None = None,
) -> CallTranscriptRevision:
    version = (
        await session.scalar(
            select(func.max(CallTranscriptRevision.version)).where(CallTranscriptRevision.call_id == call.id)
        )
        or 0
    ) + 1
    if corrected_segments is None:
        source = list(
            await session.scalars(
                select(VoiceTranscriptSegment)
                .where(VoiceTranscriptSegment.call_id == call.id, VoiceTranscriptSegment.is_final.is_(True))
                .order_by(VoiceTranscriptSegment.sequence)
            )
        )
        segments = [
            {
                "source_segment_id": item.id,
                "sequence": item.sequence,
                "speaker": item.speaker,
                "text": redact(item.text),
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "event_sequence": item.event_sequence,
            }
            for item in source
        ]
        status = "cleaned"
    else:
        segments = [
            {
                "source_segment_id": item.get("source_segment_id"),
                "sequence": index + 1,
                "speaker": item["speaker"],
                "text": redact(item["text"]),
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "event_sequence": item.get("event_sequence", 0),
            }
            for index, item in enumerate(corrected_segments)
        ]
        status = "corrected"
    redacted_text = "\n".join(f"{item['speaker']}: {item['text']}" for item in segments)
    revision = CallTranscriptRevision(
        tenant_id=call.tenant_id,
        call_id=call.id,
        source_revision_id=source_revision_id,
        version=version,
        status=status,
        segments=segments,
        redacted_text=redacted_text,
        content_hash=hashlib.sha256(redacted_text.encode()).hexdigest(),
        processor_version=PROCESSOR_VERSION,
        corrected_by_user_id=corrected_by_user_id,
        correction_reason=correction_reason,
    )
    session.add(revision)
    await session.flush()
    return revision


async def process_call(
    session: AsyncSession,
    call_id: str,
    *,
    transcript_revision_id: str | None = None,
) -> tuple[CallPipelineRun, CallIntelligence]:
    call = await session.get(VoiceCall, call_id)
    if call is None:
        raise ValueError("Voice call not found")
    if call.ended_at is None:
        raise ValueError("Voice call has not ended")
    if transcript_revision_id:
        revision = await session.scalar(
            select(CallTranscriptRevision).where(
                CallTranscriptRevision.id == transcript_revision_id,
                CallTranscriptRevision.call_id == call.id,
            )
        )
        if revision is None:
            raise ValueError("Transcript revision not found")
    else:
        revision = await session.scalar(
            select(CallTranscriptRevision)
            .where(CallTranscriptRevision.call_id == call.id)
            .order_by(CallTranscriptRevision.version.desc())
            .limit(1)
        )
        if revision is None:
            revision = await build_revision(session, call)
    pipeline_version = f"{PIPELINE_VERSION}:r{revision.version}"
    pipeline = await session.scalar(
        select(CallPipelineRun).where(
            CallPipelineRun.call_id == call.id,
            CallPipelineRun.pipeline_version == pipeline_version,
        )
    )
    existing = await session.scalar(
        select(CallIntelligence).where(CallIntelligence.transcript_revision_id == revision.id)
    )
    if pipeline is not None and pipeline.status == "completed" and existing is not None:
        return pipeline, existing
    if pipeline is None:
        pipeline = CallPipelineRun(
            tenant_id=call.tenant_id,
            call_id=call.id,
            pipeline_version=pipeline_version,
            status="running",
            checkpoints=[],
            attempt=1,
            started_at=utcnow(),
        )
        session.add(pipeline)
    else:
        pipeline.status = "running"
        pipeline.attempt += 1
        pipeline.error_message = None
        pipeline.started_at = utcnow()
    await session.flush()
    checkpoints = ["transcript_finalized", "pii_redacted"]
    try:
        events = list(
            await session.scalars(
                select(VoiceCallEvent).where(VoiceCallEvent.call_id == call.id).order_by(VoiceCallEvent.sequence)
            )
        )
        tools = list(await session.scalars(select(VoiceToolInvocation).where(VoiceToolInvocation.call_id == call.id)))
        text = revision.redacted_text
        caller_text = "\n".join(
            item["text"] for item in revision.segments if item.get("speaker") in {"caller", "customer"}
        )
        topic = classify_topic(caller_text)
        emergency = topic == "safety_emergency"
        event_types = [event.event_type for event in events]
        compliance_flags: list[dict] = []
        if call.consent_status != "granted" and call.recording_status in {"active", "stored"}:
            compliance_flags.append({"code": "recording_without_consent", "severity": "critical"})
        if emergency and "transfer.requested" not in event_types:
            compliance_flags.append({"code": "emergency_not_transferred", "severity": "critical"})
        if "consent.requested" not in event_types:
            compliance_flags.append({"code": "missing_disclosure", "severity": "high"})
        objections = []
        if call.consent_status == "declined":
            objections.append("declined_ai_or_recording_consent")
        if call.transfer_reason == "caller_requested_human":
            objections.append("requested_human")
        reason = str(call.state.get("reason") or "").strip()
        intent = (
            "emergency_help"
            if emergency
            else "request_callback" if "callback" in (call.outcome or "") else "service_request"
        )
        action_items = []
        if call.outcome == "callback_review_pending":
            action_items.append("Review callback request")
        elif call.job_id:
            action_items.append("Complete linked callback job")
        elif call.outcome == "human_transfer":
            action_items.append("Verify human handoff")
        summary_reason = redact(reason) if reason else "No service reason captured"
        summary = f"{call.direction.title()} {topic.replace('_', ' ')} call. {summary_reason}. Outcome: {call.outcome or call.status}."
        confidence = 9300 if len(revision.segments) >= 3 and reason else 6800
        if compliance_flags:
            confidence = min(confidence, 7000)
        intelligence_version = (
            await session.scalar(select(func.max(CallIntelligence.version)).where(CallIntelligence.call_id == call.id))
            or 0
        ) + 1
        intelligence = CallIntelligence(
            tenant_id=call.tenant_id,
            call_id=call.id,
            transcript_revision_id=revision.id,
            version=intelligence_version,
            extractor_version=EXTRACTOR_VERSION,
            topic=topic,
            intent=intent,
            sentiment_trajectory=sentiment(caller_text),
            objections=objections,
            compliance_flags=compliance_flags,
            action_items=action_items,
            summary=summary,
            outcome=call.outcome or call.status,
            structured_fields={
                "caller_name": call.state.get("caller_name"),
                "reason": redact(reason),
                "customer_id": call.customer_id,
                "job_id": call.job_id,
            },
            confidence_bps=confidence,
            provenance={
                "call_id": call.id,
                "transcript_revision_id": revision.id,
                "event_sequences": [event.sequence for event in events],
                "extractor_version": EXTRACTOR_VERSION,
            },
            search_text=f"{text}\n{summary}",
            status="pending_review" if confidence < 7500 or compliance_flags else "accepted",
        )
        session.add(intelligence)
        checkpoints.extend(["conversation_extracted", "search_indexed"])

        ended_at = aware(call.ended_at) if call.ended_at else utcnow()
        duration_ms = max(0, int((ended_at - aware(call.started_at)).total_seconds() * 1000))
        first_audio_values = call.latency_metrics.get("speech_end_to_first_audio_ms", [])
        first_audio = int(sum(first_audio_values) / len(first_audio_values)) if first_audio_values else 0
        fact = await session.scalar(select(CallFact).where(CallFact.call_id == call.id))
        values = {
            "customer_id": call.customer_id,
            "job_id": call.job_id,
            "provider": call.provider,
            "region": call.region,
            "flow_key": call.flow_key,
            "started_at": call.started_at,
            "duration_ms": duration_ms,
            "answered": call.answered_at is not None,
            "contained": call.outcome not in {"human_transfer", None} and call.status != "abandoned",
            "transferred": call.outcome == "human_transfer",
            "abandoned": call.status == "abandoned",
            "consented": call.consent_status == "granted",
            "interruption_count": event_types.count("playback.interrupted"),
            "silence_count": event_types.count("silence.detected"),
            "tool_count": len(tools),
            "first_audio_ms": first_audio,
            "transcript_segments": len(revision.segments),
            "estimated_cost_microusd": max(1, duration_ms // 1000) * 10 + len(text),
            "outcome": call.outcome or call.status,
        }
        if fact is None:
            fact = CallFact(tenant_id=call.tenant_id, call_id=call.id, **values)
            session.add(fact)
        else:
            for key, value in values.items():
                setattr(fact, key, value)
        checkpoints.append("fact_modeled")

        expected_sequences = list(range(1, len(events) + 1))
        actual_sequences = [event.sequence for event in events]
        discrepancies = []
        if actual_sequences != expected_sequences:
            discrepancies.append("event_sequence_gap")
        if call.ended_at is None:
            discrepancies.append("missing_end_time")
        reconciliation = await session.scalar(
            select(CallReconciliation).where(
                CallReconciliation.call_id == call.id,
                CallReconciliation.provider_snapshot_id == f"internal:{call.provider_call_id}",
            )
        )
        if reconciliation is None:
            reconciliation = CallReconciliation(
                tenant_id=call.tenant_id,
                call_id=call.id,
                provider_snapshot_id=f"internal:{call.provider_call_id}",
                provider_status=call.status,
                provider_duration_ms=duration_ms,
                internal_status=call.status,
                internal_duration_ms=duration_ms,
                complete=not discrepancies,
                discrepancies=discrepancies,
                reconciled_at=utcnow(),
            )
            session.add(reconciliation)
        checkpoints.append("reconciled")
        pipeline.status = "completed"
        pipeline.checkpoints = checkpoints
        pipeline.completed_at = utcnow()
        await session.flush()
        return pipeline, intelligence
    except Exception as exc:
        pipeline.status = "failed"
        pipeline.checkpoints = checkpoints
        pipeline.error_message = str(exc)[:2000]
        raise
