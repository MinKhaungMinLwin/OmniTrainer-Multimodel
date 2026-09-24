import asyncio
import base64
import hashlib
import json
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from services.api.omni_api.config import Settings
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import CallRawPayload, VoiceCall, VoiceFlowConfig
from services.api.omni_api.storage import AttachmentStorage
from services.observability import configure_tracing, shutdown_tracing
from services.voice.omni_voice.providers import GenericTelephonyAdapter, LocalStreamingSTT, normalize_audio
from services.voice.omni_voice.reliability import (
    AsyncBoundedMediaBuffer,
    CircuitBreaker,
    ReliabilityMetrics,
    ReliableProvider,
    SessionAdmission,
)
from services.voice.omni_voice.runtime import (
    AdmissionError,
    VoiceOutput,
    admit_call,
    answer_call,
    append_event,
    append_transcript,
    handle_dtmf,
    handle_silence,
    handle_user_turn,
    handle_voicemail,
    hangup_call,
    speech_started,
    store_recording,
)


class TelephonyWebhook(BaseModel):
    event: Literal["incoming", "answered", "completed", "failed"]
    tenant_id: str
    provider: str = "generic"
    provider_call_id: str
    caller: str
    callee: str
    region: str = "local"
    flow_key: str = "after_hours_intake"
    reason: str | None = None


class WebRtcOffer(BaseModel):
    call_id: str
    token: str
    sdp: str = Field(min_length=1, max_length=100_000)


def serialize_output(output: VoiceOutput) -> dict:
    return {
        "type": output.event_type,
        "text": output.text,
        "generation_id": output.generation_id,
        "transfer_number": output.transfer_number,
        "audio": [base64.b64encode(chunk).decode() for chunk in output.audio_chunks or []],
    }


def create_voice_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()
    engine = build_engine(app_settings.database_url)
    session_factory = build_session_factory(engine)
    adapter = GenericTelephonyAdapter(
        app_settings.voice_webhook_secret,
        app_settings.voice_media_secret,
        app_settings.voice_webhook_tolerance_seconds,
    )
    reliability = ReliabilityMetrics()
    session_admission = SessionAdmission(app_settings.voice_max_gateway_sessions, reliability)
    stt_breaker = CircuitBreaker(
        app_settings.voice_circuit_failure_threshold,
        app_settings.voice_circuit_reset_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tracing_provider = configure_tracing(app_settings, "omni-voice")
        app.state.session_factory = session_factory
        try:
            yield
        finally:
            await engine.dispose()
            shutdown_tracing(tracing_provider)

    app = FastAPI(title="Omni Voice Media Gateway", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.telephony_adapter = adapter
    app.state.reliability = reliability

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/health/providers")
    async def provider_health():
        return {"stt": {"provider": "local-stt", "circuit": stt_breaker.state}, "status": "ok"}

    @app.get("/v1/reliability")
    async def reliability_status():
        return reliability.snapshot()

    @app.get("/metrics")
    async def metrics():
        return Response(reliability.prometheus(), media_type="text/plain; version=0.0.4")

    @app.post("/v1/webhooks/telephony")
    async def telephony_webhook(
        request: Request,
        x_voice_timestamp: str = Header(alias="X-Voice-Timestamp"),
        x_voice_signature: str = Header(alias="X-Voice-Signature"),
        x_voice_event_id: str | None = Header(default=None, alias="X-Voice-Event-ID"),
    ):
        body = await request.body()
        if not adapter.verify_webhook(body, x_voice_timestamp, x_voice_signature):
            raise HTTPException(status_code=401, detail="Invalid or stale voice webhook signature")
        payload = TelephonyWebhook.model_validate_json(body)
        async with session_factory() as session:

            async def store_raw(call: VoiceCall) -> None:
                event_id = x_voice_event_id or hashlib.sha256(f"{x_voice_timestamp}.".encode() + body).hexdigest()
                existing = await session.scalar(
                    select(CallRawPayload).where(
                        CallRawPayload.provider == payload.provider,
                        CallRawPayload.provider_event_id == event_id,
                    )
                )
                if existing is not None:
                    return
                digest = hashlib.sha256(body).hexdigest()
                object_key = f"call-data/raw/{call.tenant_id}/{call.id}/{digest}.json"
                AttachmentStorage(app_settings).put(object_key, body, "application/json")
                config = await session.get(VoiceFlowConfig, call.flow_config_id)
                received_at = datetime.now(timezone.utc)
                session.add(
                    CallRawPayload(
                        tenant_id=call.tenant_id,
                        call_id=call.id,
                        provider=payload.provider,
                        provider_call_id=payload.provider_call_id,
                        provider_event_id=event_id,
                        event_type=payload.event,
                        schema_version="1",
                        payload_hash=digest,
                        object_key=object_key,
                        received_at=received_at,
                        retention_until=received_at + timedelta(days=config.retention_days),
                        legal_hold=False,
                    )
                )

            if payload.event == "incoming":
                try:
                    call, created = await admit_call(
                        session,
                        tenant_id=payload.tenant_id,
                        provider=payload.provider,
                        provider_call_id=payload.provider_call_id,
                        caller=payload.caller,
                        callee=payload.callee,
                        region=payload.region,
                        flow_key=payload.flow_key,
                    )
                except AdmissionError as exc:
                    raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from None
                await store_raw(call)
                await session.commit()
                expires_at = int(time.time()) + 3600
                token = adapter.media_token(call.id, expires_at)
                base = (
                    app_settings.voice_public_base_url.rstrip("/")
                    .replace("http://", "ws://")
                    .replace("https://", "wss://")
                )
                return {
                    "call_id": call.id,
                    "created": created,
                    "status": call.status,
                    "media_url": f"{base}/v1/media/{call.id}?token={token}",
                    "expires_at": expires_at,
                }
            call = await session.scalar(
                select(VoiceCall).where(
                    VoiceCall.provider == payload.provider,
                    VoiceCall.provider_call_id == payload.provider_call_id,
                )
            )
            if call is None:
                raise HTTPException(status_code=404, detail="Call not found")
            await store_raw(call)
            if payload.event == "answered":
                await answer_call(session, call)
            elif payload.event == "completed":
                await hangup_call(session, call, payload.reason or "provider_completed")
            else:
                call.status = "failed"
                call.outcome = "provider_failure"
                await append_event(session, call, "call.failed", {"reason": payload.reason})
                await hangup_call(session, call, payload.reason or "provider_failed")
            await session.commit()
            return {"call_id": call.id, "status": call.status}

    @app.post("/v1/webrtc/offer")
    async def webrtc_offer(payload: WebRtcOffer):
        if not adapter.verify_media_token(payload.token, payload.call_id):
            raise HTTPException(status_code=401, detail="Invalid media token")
        async with session_factory() as session:
            call = await session.get(VoiceCall, payload.call_id)
            if call is None:
                raise HTTPException(status_code=404, detail="Call not found")
            await append_event(session, call, "webrtc.offer.received", {"sdp_bytes": len(payload.sdp.encode())})
            await session.commit()
        base = app_settings.voice_public_base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
        return {
            "type": "answer",
            "sdp": "v=0\r\no=omni 0 0 IN IP4 127.0.0.1\r\ns=Omni Voice\r\nt=0 0\r\na=group:BUNDLE 0\r\n",
            "media_bridge": f"{base}/v1/media/{payload.call_id}?token={payload.token}",
            "codecs": ["opus/48000", "PCMU/8000", "PCMA/8000"],
        }

    @app.websocket("/v1/media/{call_id}")
    async def media_stream(websocket: WebSocket, call_id: str, token: str):
        if not adapter.verify_media_token(token, call_id):
            await websocket.close(code=4401, reason="Invalid media token")
            return
        if not await session_admission.acquire():
            await websocket.close(code=4429, reason="Gateway capacity reached; retry on failover")
            return
        try:
            await websocket.accept()
        except Exception:
            await session_admission.release()
            raise
        audio_buffer = bytearray()
        codec = "mulaw"
        sample_rate_hz = 8000
        duration_ms = 0
        stt = LocalStreamingSTT()
        fallback_stt = LocalStreamingSTT()
        media_buffer = AsyncBoundedMediaBuffer(
            app_settings.voice_max_buffered_frames,
            app_settings.voice_stale_frame_ms,
            reliability,
        )

        async def receive_media() -> None:
            try:
                while True:
                    raw_frame = await websocket.receive_text()
                    if len(raw_frame.encode()) > app_settings.voice_max_frame_bytes:
                        reliability.increment("media_frames_oversized_total")
                        continue
                    try:
                        incoming = json.loads(raw_frame)
                    except json.JSONDecodeError:
                        reliability.increment("media_frames_invalid_total")
                        continue
                    await media_buffer.push(incoming)
            except WebSocketDisconnect:
                pass
            finally:
                await media_buffer.close()

        receiver = asyncio.create_task(receive_media())
        try:
            while True:
                buffered = await media_buffer.pop()
                if buffered is None:
                    raise WebSocketDisconnect
                frame = buffered.payload
                async with session_factory() as session:
                    call = await session.get(VoiceCall, call_id)
                    if call is None:
                        await websocket.close(code=4404, reason="Call not found")
                        return
                    frame_type = frame.get("type")
                    output: VoiceOutput | None = None
                    if frame_type == "start":
                        codec = str(frame.get("codec", "mulaw"))
                        sample_rate_hz = int(frame.get("sample_rate_hz", 8000))
                        await append_event(
                            session, call, "media.started", {"codec": codec, "sample_rate_hz": sample_rate_hz}
                        )
                        output = await answer_call(session, call)
                    elif frame_type == "speech_start":
                        output = await speech_started(session, call, int(frame.get("heard_response_boundary_ms", 0)))
                    elif frame_type == "audio":
                        chunk = base64.b64decode(frame.get("audio", ""), validate=True)
                        duration_ms += int(frame.get("duration_ms", 20))
                        if len(audio_buffer) + len(chunk) <= app_settings.voice_max_recording_bytes:
                            audio_buffer.extend(chunk)
                        else:
                            await append_event(
                                session,
                                call,
                                "recording.truncated",
                                {"limit_bytes": app_settings.voice_max_recording_bytes},
                            )
                        stt_started = time.perf_counter_ns()
                        normalized = normalize_audio(chunk, codec, sample_rate_hz)
                        provider = ReliableProvider(
                            "stt",
                            lambda: stt.transcribe(normalized, frame),
                            lambda: fallback_stt.transcribe(b"", frame),
                            timeout_seconds=app_settings.voice_stage_timeout_ms / 1000,
                            breaker=stt_breaker,
                            metrics=reliability,
                        )
                        provider_result = await provider.run()
                        hypothesis = provider_result.value
                        if provider_result.degraded:
                            await append_event(
                                session,
                                call,
                                "provider.fallback.activated",
                                {"stage": "stt", "provider": provider_result.provider},
                            )
                        stt_ms = max(0, int((time.perf_counter_ns() - stt_started) / 1_000_000))
                        metrics = {
                            key: list(value) if isinstance(value, list) else value
                            for key, value in call.latency_metrics.items()
                        }
                        metrics.setdefault("stt_ms", []).append(stt_ms)
                        call.latency_metrics = metrics
                        if hypothesis and hypothesis.is_final:
                            output = await handle_user_turn(
                                session,
                                call,
                                hypothesis.text,
                                start_ms=int(frame.get("start_ms", max(0, duration_ms - 20))),
                                end_ms=int(frame.get("end_ms", duration_ms)),
                                confidence_bps=hypothesis.confidence_bps,
                            )
                        elif hypothesis:
                            await append_transcript(
                                session,
                                call,
                                speaker="caller",
                                text=hypothesis.text,
                                start_ms=int(frame.get("start_ms", max(0, duration_ms - 20))),
                                end_ms=int(frame.get("end_ms", duration_ms)),
                                provider="local-stt",
                                confidence_bps=hypothesis.confidence_bps,
                                is_final=False,
                            )
                    elif frame_type == "transcript":
                        output = await handle_user_turn(
                            session,
                            call,
                            str(frame.get("text", "")),
                            start_ms=int(frame.get("start_ms", duration_ms)),
                            end_ms=int(frame.get("end_ms", duration_ms + 500)),
                            confidence_bps=int(frame.get("confidence_bps", 9900)),
                        )
                    elif frame_type == "playback_complete":
                        call.state = {**call.state, "agent_speaking": False}
                        await append_event(
                            session,
                            call,
                            "audio.playback.completed",
                            {"generation_id": frame.get("generation_id")},
                        )
                    elif frame_type == "dtmf":
                        output = await handle_dtmf(session, call, str(frame.get("digit", "")), duration_ms)
                    elif frame_type == "silence":
                        output = await handle_silence(session, call)
                    elif frame_type == "voicemail":
                        output = await handle_voicemail(session, call)
                    elif frame_type == "hangup":
                        await hangup_call(session, call)
                        await store_recording(
                            session,
                            app_settings,
                            call,
                            bytes(audio_buffer),
                            codec=codec,
                            sample_rate_hz=sample_rate_hz,
                            duration_ms=duration_ms,
                        )
                        await session.commit()
                        await websocket.send_json({"type": "ended", "call_id": call.id})
                        await websocket.close(code=1000)
                        return
                    else:
                        await append_event(session, call, "media.unknown_frame", {"type": frame_type})
                    await session.commit()
                    if output is not None:
                        await websocket.send_json(serialize_output(output))
        except WebSocketDisconnect:
            async with session_factory() as session:
                call = await session.get(VoiceCall, call_id)
                if call is not None:
                    await hangup_call(session, call, "media_disconnect")
                    await store_recording(
                        session,
                        app_settings,
                        call,
                        bytes(audio_buffer),
                        codec=codec,
                        sample_rate_hz=sample_rate_hz,
                        duration_ms=duration_ms,
                    )
                    await session.commit()
        finally:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
            await media_buffer.close()
            await session_admission.release()

    return app


app = create_voice_app()


def main() -> None:
    import uvicorn

    uvicorn.run("services.voice.omni_voice.main:app", host="0.0.0.0", port=8002, reload=True)


if __name__ == "__main__":
    main()
