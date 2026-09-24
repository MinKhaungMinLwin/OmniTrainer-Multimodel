import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openinference.semconv.trace import OpenInferenceSpanKindValues
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.config import Settings
from services.api.omni_api.models import (
    Customer,
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
from services.observability import hash_identifier, traced_span
from services.voice.omni_voice.providers import LocalRealtimeModel, LocalStreamingTTS

_LOCAL_MODEL = LocalRealtimeModel()
_LOCAL_TTS = LocalStreamingTTS()


@dataclass(frozen=True)
class VoiceOutput:
    event_type: str
    text: str | None = None
    audio_chunks: list[bytes] | None = None
    transfer_number: str | None = None
    generation_id: int | None = None


class AdmissionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def append_event(session: AsyncSession, call: VoiceCall, event_type: str, payload: dict | None = None) -> int:
    call.last_sequence += 1
    sequence = call.last_sequence
    session.add(
        VoiceCallEvent(
            tenant_id=call.tenant_id,
            call_id=call.id,
            sequence=sequence,
            schema_version="1",
            event_type=event_type,
            payload=payload or {},
            occurred_at=utcnow(),
        )
    )
    return sequence


async def append_transcript(
    session: AsyncSession,
    call: VoiceCall,
    *,
    speaker: str,
    text: str,
    start_ms: int,
    end_ms: int,
    provider: str,
    confidence_bps: int | None = None,
    is_final: bool = True,
) -> VoiceTranscriptSegment:
    sequence = (
        await session.scalar(
            select(func.max(VoiceTranscriptSegment.sequence)).where(VoiceTranscriptSegment.call_id == call.id)
        )
        or 0
    ) + 1
    event_sequence = await append_event(
        session,
        call,
        "transcript.final" if is_final else "transcript.partial",
        {"speaker": speaker, "text": text, "start_ms": start_ms, "end_ms": end_ms},
    )
    segment = VoiceTranscriptSegment(
        tenant_id=call.tenant_id,
        call_id=call.id,
        sequence=sequence,
        speaker=speaker,
        text=text,
        is_final=is_final,
        start_ms=start_ms,
        end_ms=end_ms,
        event_sequence=event_sequence,
        provider=provider,
        confidence_bps=confidence_bps,
    )
    session.add(segment)
    return segment


def within_allowed_hours(config: VoiceFlowConfig, moment: datetime) -> bool:
    rules = config.allowed_hours or {}
    if not rules:
        return True
    try:
        local = moment.astimezone(ZoneInfo(str(rules.get("timezone", "UTC"))))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    try:
        days = rules.get("days", list(range(7)))
        if local.weekday() not in days:
            return False
        start_hour, start_minute = map(int, str(rules.get("start", "00:00")).split(":"))
        end_hour, end_minute = map(int, str(rules.get("end", "23:59")).split(":"))
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23 and 0 <= start_minute <= 59 and 0 <= end_minute <= 59):
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    minute = local.hour * 60 + local.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    return start <= minute <= end if start <= end else minute >= start or minute <= end


async def admit_call(
    session: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    provider_call_id: str,
    caller: str,
    callee: str,
    region: str,
    flow_key: str = "after_hours_intake",
) -> tuple[VoiceCall, bool]:
    existing = await session.scalar(
        select(VoiceCall).where(
            VoiceCall.provider == provider,
            VoiceCall.provider_call_id == provider_call_id,
        )
    )
    if existing is not None:
        return existing, False
    config = await session.scalar(
        select(VoiceFlowConfig).where(
            VoiceFlowConfig.tenant_id == tenant_id,
            VoiceFlowConfig.flow_key == flow_key,
        )
    )
    if config is None or not config.enabled:
        raise AdmissionError("flow_disabled", "Voice pilot is disabled for this tenant")
    if config.allowed_regions and region not in config.allowed_regions:
        raise AdmissionError("region_denied", "Call region is outside the pilot cohort")
    if not within_allowed_hours(config, utcnow()):
        raise AdmissionError("outside_hours", "Call is outside configured pilot hours")
    concurrent = await session.scalar(
        select(func.count(VoiceCall.id)).where(
            VoiceCall.tenant_id == tenant_id,
            VoiceCall.status.in_(["admitted", "in_progress", "awaiting_review", "transferring"]),
        )
    )
    if (concurrent or 0) >= config.max_concurrent_calls:
        raise AdmissionError("capacity", "Voice pilot concurrency limit reached")
    call = VoiceCall(
        tenant_id=tenant_id,
        flow_config_id=config.id,
        provider=provider,
        provider_call_id=provider_call_id,
        direction="inbound",
        caller=caller,
        callee=callee,
        flow_key=flow_key,
        region=region,
        status="admitted",
        consent_status="pending",
        recording_status="pending",
        state={"phase": "awaiting_consent", "agent_speaking": False, "generation_id": 0, "silence_count": 0},
        latency_metrics={"stt_ms": [], "model_ms": [], "tts_ms": [], "speech_end_to_first_audio_ms": []},
        started_at=utcnow(),
    )
    session.add(call)
    await session.flush()
    await append_event(
        session,
        call,
        "call.admitted",
        {"provider": provider, "provider_call_id": provider_call_id, "region": region, "flow_key": flow_key},
    )
    return call, True


async def agent_reply(
    session: AsyncSession,
    call: VoiceCall,
    text: str,
    *,
    speech_end_ns: int | None = None,
) -> VoiceOutput:
    model_started = time.perf_counter_ns()
    response = await _LOCAL_MODEL.generate(text, call.state)
    model_ms = max(0, int((time.perf_counter_ns() - model_started) / 1_000_000))
    tts_started = time.perf_counter_ns()
    chunks = await _LOCAL_TTS.synthesize(response)
    tts_ms = max(0, int((time.perf_counter_ns() - tts_started) / 1_000_000))
    first_audio_ms = max(0, int((time.perf_counter_ns() - speech_end_ns) / 1_000_000)) if speech_end_ns else 0
    metrics = {key: list(value) if isinstance(value, list) else value for key, value in call.latency_metrics.items()}
    metrics.setdefault("model_ms", []).append(model_ms)
    metrics.setdefault("tts_ms", []).append(tts_ms)
    if speech_end_ns:
        metrics.setdefault("speech_end_to_first_audio_ms", []).append(first_audio_ms)
    call.latency_metrics = metrics
    state = dict(call.state)
    state["generation_id"] = int(state.get("generation_id", 0)) + 1
    state["agent_speaking"] = True
    call.state = state
    started_at = call.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed_ms = max(1, int((utcnow() - started_at).total_seconds() * 1000))
    await append_transcript(
        session,
        call,
        speaker="assistant",
        text=response,
        start_ms=elapsed_ms,
        end_ms=elapsed_ms + max(200, len(response) * 35),
        provider="local-tts",
    )
    await append_event(
        session,
        call,
        "audio.playback.started",
        {
            "generation_id": state["generation_id"],
            "chunk_count": len(chunks),
            "model_ms": model_ms,
            "tts_ms": tts_ms,
            "speech_end_to_first_audio_ms": first_audio_ms,
        },
    )
    return VoiceOutput(
        event_type="playback",
        text=response,
        audio_chunks=chunks,
        generation_id=state["generation_id"],
    )


async def answer_call(session: AsyncSession, call: VoiceCall) -> VoiceOutput:
    if call.status == "admitted":
        call.status = "in_progress"
        call.answered_at = utcnow()
        await append_event(session, call, "call.answered", {})
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    disclosure = config.disclosure_text
    await append_event(
        session,
        call,
        "consent.requested",
        {"ai_required": config.require_ai_consent, "recording_required": config.require_recording_consent},
    )
    return await agent_reply(session, call, disclosure)


async def speech_started(session: AsyncSession, call: VoiceCall, heard_boundary_ms: int = 0) -> VoiceOutput | None:
    state = dict(call.state)
    await append_event(session, call, "speech.started", {"heard_response_boundary_ms": heard_boundary_ms})
    if not state.get("agent_speaking"):
        return None
    cancelled_generation = int(state.get("generation_id", 0))
    state["agent_speaking"] = False
    state["heard_response_boundary_ms"] = heard_boundary_ms
    call.state = state
    await append_event(
        session,
        call,
        "playback.interrupted",
        {"generation_id": cancelled_generation, "heard_response_boundary_ms": heard_boundary_ms, "queue_flushed": True},
    )
    return VoiceOutput(event_type="clear_playback", generation_id=cancelled_generation)


async def lookup_customer(session: AsyncSession, call: VoiceCall) -> Customer | None:
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    if "find_customer" not in config.allowed_tools:
        await append_event(session, call, "tool.denied", {"name": "find_customer", "reason": "not allowlisted"})
        return None
    started = time.perf_counter_ns()
    invocation = VoiceToolInvocation(
        tenant_id=call.tenant_id,
        call_id=call.id,
        name="find_customer",
        status="running",
        input={"phone": call.caller},
        latency_ms=0,
    )
    session.add(invocation)
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == call.tenant_id,
            or_(Customer.phone == call.caller, Customer.email == call.caller),
        )
    )
    invocation.status = "completed"
    invocation.output = {"customer_id": customer.id, "name": customer.name} if customer else {"customer_id": None}
    invocation.latency_ms = max(0, int((time.perf_counter_ns() - started) / 1_000_000))
    call.customer_id = customer.id if customer else None
    await append_event(
        session,
        call,
        "tool.completed",
        {"name": "find_customer", "found": customer is not None, "latency_ms": invocation.latency_ms},
    )
    return customer


def is_yes(text: str) -> bool:
    normalized = text.lower().strip()
    return any(token in normalized for token in ("yes", "okay", "ok", "agree", "consent", "sure", "1"))


def is_no(text: str) -> bool:
    normalized = text.lower().strip()
    return any(token in normalized for token in ("no", "decline", "do not", "don't", "2"))


async def request_transfer(
    session: AsyncSession, call: VoiceCall, reason: str, text: str = "I’m connecting you with a person now."
) -> VoiceOutput:
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    call.status = "transferring"
    call.outcome = "human_transfer"
    call.transfer_reason = reason
    await append_event(
        session,
        call,
        "transfer.requested",
        {"reason": reason, "transfer_number": config.transfer_number},
    )
    output = await agent_reply(session, call, text)
    return VoiceOutput(
        event_type="transfer",
        text=output.text,
        audio_chunks=output.audio_chunks,
        transfer_number=config.transfer_number,
        generation_id=output.generation_id,
    )


async def handle_user_turn(
    session: AsyncSession,
    call: VoiceCall,
    text: str,
    *,
    start_ms: int,
    end_ms: int,
    confidence_bps: int = 9900,
) -> VoiceOutput:
    with traced_span(
        "voice.turn",
        OpenInferenceSpanKindValues.AGENT,
        session_id=call.id,
        metadata={"tenant_hash": hash_identifier(call.tenant_id), "call_id": call.id},
        attributes={
            "voice.input_characters": len(text),
            "voice.input_duration_ms": max(0, end_ms - start_ms),
            "voice.confidence_bps": confidence_bps,
            "voice.phase": str(call.state.get("phase", "awaiting_consent")),
        },
    ) as span:
        output = await _handle_user_turn(
            session,
            call,
            text,
            start_ms=start_ms,
            end_ms=end_ms,
            confidence_bps=confidence_bps,
        )
        span.set_attribute("voice.output_type", output.event_type)
        span.set_attribute("voice.output_characters", len(output.text or ""))
        return output


async def _handle_user_turn(
    session: AsyncSession,
    call: VoiceCall,
    text: str,
    *,
    start_ms: int,
    end_ms: int,
    confidence_bps: int = 9900,
) -> VoiceOutput:
    speech_end_ns = time.perf_counter_ns()
    await append_transcript(
        session,
        call,
        speaker="caller",
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        provider="local-stt",
        confidence_bps=confidence_bps,
    )
    state = dict(call.state)
    state["agent_speaking"] = False
    call.state = state
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    normalized = text.lower()
    if any(keyword.lower() in normalized for keyword in config.emergency_keywords):
        return await request_transfer(
            session,
            call,
            "emergency_keyword",
            "This may be an emergency. Please contact emergency services. I’m also transferring you now.",
        )
    phase = state.get("phase", "awaiting_consent")
    if phase == "awaiting_consent":
        if is_no(text):
            call.consent_status = "declined"
            call.recording_status = "disabled"
            await append_event(session, call, "consent.declined", {})
            return await request_transfer(session, call, "consent_declined")
        if not is_yes(text):
            return await agent_reply(
                session, call, "Please say yes to continue, or no to speak with a person.", speech_end_ns=speech_end_ns
            )
        call.consent_status = "granted"
        call.recording_status = "active"
        state["phase"] = "collect_name"
        call.state = state
        await append_event(session, call, "consent.granted", {"recording": True})
        return await agent_reply(session, call, "Thank you. What is your name?", speech_end_ns=speech_end_ns)
    if phase == "collect_name":
        state["caller_name"] = text.strip()[:200]
        state["phase"] = "collect_reason"
        call.state = state
        return await agent_reply(session, call, "How can our team help you?", speech_end_ns=speech_end_ns)
    if phase == "collect_reason":
        state["reason"] = text.strip()[:2000]
        state["phase"] = "confirm"
        call.state = state
        customer = await lookup_customer(session, call)
        greeting = f"I found your account, {customer.name}. " if customer else ""
        return await agent_reply(
            session,
            call,
            f"{greeting}I heard: {state['reason']}. Should I ask the team to call you back?",
            speech_end_ns=speech_end_ns,
        )
    if phase == "confirm":
        if is_no(text):
            return await request_transfer(session, call, "caller_requested_human")
        if not is_yes(text):
            return await agent_reply(
                session,
                call,
                "Please say yes for a reviewed callback request, or no for a person.",
                speech_end_ns=speech_end_ns,
            )
        review = await session.scalar(select(VoiceReview).where(VoiceReview.call_id == call.id))
        if review is None:
            review = VoiceReview(
                tenant_id=call.tenant_id,
                call_id=call.id,
                status="pending",
                action="create_callback_job",
                proposed_args={
                    "customer_id": call.customer_id,
                    "name": state.get("caller_name", "Caller"),
                    "phone": call.caller,
                    "reason": state.get("reason", "Callback requested"),
                    "title": f"Callback: {state.get('reason', 'new request')[:140]}",
                },
            )
            session.add(review)
        call.status = "awaiting_review"
        call.outcome = "callback_review_pending"
        state["phase"] = "complete"
        call.state = state
        await append_event(session, call, "review.requested", {"action": "create_callback_job"})
        output = await agent_reply(
            session,
            call,
            "Thanks. A person will review your callback request before anything is created. Goodbye.",
            speech_end_ns=speech_end_ns,
        )
        if "create_callback_job" not in config.allowed_tools:
            return await request_transfer(
                session,
                call,
                "tool_not_allowlisted",
                "I can’t create a callback request in this flow, so I’ll connect you with a person.",
            )
        call.ended_at = utcnow()
        await append_event(session, call, "call.completed", {"outcome": call.outcome})
        return output
    return VoiceOutput(event_type="completed", text="This call is complete.", audio_chunks=[])


async def handle_dtmf(session: AsyncSession, call: VoiceCall, digit: str, elapsed_ms: int) -> VoiceOutput:
    await append_event(session, call, "dtmf.received", {"digit": digit, "elapsed_ms": elapsed_ms})
    if digit == "0":
        return await request_transfer(session, call, "dtmf_zero")
    if call.state.get("phase") == "awaiting_consent" and digit in {"1", "2"}:
        return await handle_user_turn(
            session, call, "yes" if digit == "1" else "no", start_ms=elapsed_ms, end_ms=elapsed_ms
        )
    return await agent_reply(session, call, "Press 0 for a person, or continue speaking.")


async def handle_silence(session: AsyncSession, call: VoiceCall) -> VoiceOutput:
    state = dict(call.state)
    state["silence_count"] = int(state.get("silence_count", 0)) + 1
    call.state = state
    await append_event(session, call, "silence.detected", {"count": state["silence_count"]})
    if state["silence_count"] >= 2:
        return await request_transfer(
            session, call, "repeated_silence", "I’m having trouble hearing you, so I’ll connect you with a person."
        )
    return await agent_reply(session, call, "I didn’t hear anything. You can speak now, or press 0 for a person.")


async def handle_voicemail(session: AsyncSession, call: VoiceCall) -> VoiceOutput:
    call.status = "completed"
    call.outcome = "voicemail"
    call.ended_at = utcnow()
    await append_event(session, call, "voicemail.detected", {})
    output = await agent_reply(session, call, "We called from Omni Services. Please call us back when convenient.")
    await append_event(session, call, "call.completed", {"outcome": "voicemail"})
    return output


async def hangup_call(session: AsyncSession, call: VoiceCall, reason: str = "remote_hangup") -> None:
    already_ended = call.ended_at is not None
    if call.status == "transferring":
        call.status = "transferred"
    elif call.status not in {"completed", "failed", "transferred"}:
        call.status = "completed" if call.outcome else "abandoned"
    call.ended_at = call.ended_at or utcnow()
    call.state = {**call.state, "agent_speaking": False}
    if not already_ended:
        await append_event(session, call, "call.ended", {"reason": reason, "outcome": call.outcome})
    pending_pipeline = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "voice.call.ended",
            OutboxEvent.aggregate_id == call.id,
        )
    )
    if pending_pipeline is None:
        session.add(
            OutboxEvent(
                tenant_id=call.tenant_id,
                event_type="voice.call.ended",
                aggregate_type="voice_call",
                aggregate_id=call.id,
                payload={"call_id": call.id, "provider": call.provider},
                occurred_at=utcnow(),
                correlation_id=f"voice-call:{call.id}",
            )
        )


async def store_recording(
    session: AsyncSession,
    settings: Settings,
    call: VoiceCall,
    audio: bytes,
    *,
    codec: str,
    sample_rate_hz: int,
    duration_ms: int,
) -> VoiceRecording | None:
    if call.recording_status != "active" or not audio:
        return None
    existing = await session.scalar(select(VoiceRecording).where(VoiceRecording.call_id == call.id))
    if existing is not None:
        return existing
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    object_key = f"voice/{call.tenant_id}/{call.id}/recording.{codec}"
    AttachmentStorage(settings).put(object_key, audio, "application/octet-stream")
    recording = VoiceRecording(
        tenant_id=call.tenant_id,
        call_id=call.id,
        object_key=object_key,
        content_type="application/octet-stream",
        codec=codec,
        sample_rate_hz=sample_rate_hz,
        size_bytes=len(audio),
        duration_ms=duration_ms,
        retention_until=utcnow() + timedelta(days=config.retention_days),
        legal_hold=False,
    )
    session.add(recording)
    call.recording_status = "stored"
    await append_event(
        session,
        call,
        "recording.stored",
        {"object_key": object_key, "size_bytes": len(audio), "duration_ms": duration_ms},
    )
    return recording
