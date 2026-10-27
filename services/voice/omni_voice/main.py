import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.config import Settings
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import CallRawPayload, VoiceCall, VoiceFlowConfig
from services.api.omni_api.storage import AttachmentStorage
from services.observability import configure_tracing, shutdown_tracing
from services.voice.omni_voice import twilio
from services.voice.omni_voice.media import FrameResult, MediaSession
from services.voice.omni_voice.providers import GenericTelephonyAdapter, build_tts, build_utterance_stt
from services.voice.omni_voice.reliability import (
    AsyncBoundedMediaBuffer,
    CircuitBreaker,
    ReliabilityMetrics,
    SessionAdmission,
)
from services.voice.omni_voice.runtime import (
    AdmissionError,
    VoiceOutput,
    admit_call,
    answer_call,
    append_event,
    hangup_call,
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


async def store_raw_payload(
    session: AsyncSession,
    settings: Settings,
    call: VoiceCall,
    *,
    provider: str,
    provider_call_id: str,
    event_id: str,
    event_type: str,
    body: bytes,
) -> None:
    """Keep the provider's evidence immutable and idempotent per provider event."""

    existing = await session.scalar(
        select(CallRawPayload).where(
            CallRawPayload.provider == provider,
            CallRawPayload.provider_event_id == event_id,
        )
    )
    if existing is not None:
        return
    digest = hashlib.sha256(body).hexdigest()
    object_key = f"call-data/raw/{call.tenant_id}/{call.id}/{digest}.json"
    AttachmentStorage(settings).put(object_key, body, "application/json")
    config = await session.get(VoiceFlowConfig, call.flow_config_id)
    received_at = datetime.now(timezone.utc)
    session.add(
        CallRawPayload(
            tenant_id=call.tenant_id,
            call_id=call.id,
            provider=provider,
            provider_call_id=provider_call_id,
            provider_event_id=event_id,
            event_type=event_type,
            schema_version="1",
            payload_hash=digest,
            object_key=object_key,
            received_at=received_at,
            retention_until=received_at + timedelta(days=config.retention_days),
            legal_hold=False,
        )
    )


def websocket_base_url(settings: Settings) -> str:
    return settings.voice_public_base_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")


def create_voice_app(
    settings: Settings | None = None,
    *,
    twilio_client: twilio.TwilioClient | None = None,
) -> FastAPI:
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
    if twilio_client is None and app_settings.twilio_account_sid and app_settings.twilio_auth_token:
        twilio_client = twilio.TwilioClient(
            app_settings.twilio_account_sid,
            app_settings.twilio_auth_token,
            app_settings.twilio_api_base_url,
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
    # Factories live on app state so tests and deployments can swap providers.
    app.state.utterance_stt = build_utterance_stt(app_settings)
    app.state.tts_factory = lambda codec, sample_rate_hz: build_tts(app_settings, codec, sample_rate_hz)
    app.state.twilio_client = twilio_client

    def new_media_session(call_id: str) -> MediaSession:
        return MediaSession(
            settings=app_settings,
            session_factory=session_factory,
            call_id=call_id,
            reliability=reliability,
            stt_breaker=stt_breaker,
            utterance_stt=app.state.utterance_stt,
            tts_factory=app.state.tts_factory,
        )

    async def pump_frames(
        websocket: WebSocket, media_buffer: AsyncBoundedMediaBuffer, parse: Callable[[dict], dict | None]
    ) -> None:
        try:
            while True:
                raw_frame = await websocket.receive_text()
                if len(raw_frame.encode()) > app_settings.voice_max_frame_bytes:
                    reliability.increment("media_frames_oversized_total")
                    continue
                try:
                    incoming = parse(json.loads(raw_frame))
                except (json.JSONDecodeError, ValueError, TypeError):
                    reliability.increment("media_frames_invalid_total")
                    continue
                if incoming is not None:
                    await media_buffer.push(incoming)
        except WebSocketDisconnect:
            pass
        finally:
            await media_buffer.close()

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
        utterance_stt = app.state.utterance_stt
        return {
            "stt": {
                "provider": utterance_stt.name if utterance_stt else "local-stt",
                "circuit": stt_breaker.state,
            },
            "tts": {"provider": f"{app_settings.voice_tts_provider}-tts"},
            "telephony": {"twilio": "configured" if app.state.twilio_client else "not_configured"},
            "status": "ok",
        }

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
                await store_raw_payload(
                    session,
                    app_settings,
                    call,
                    provider=payload.provider,
                    provider_call_id=payload.provider_call_id,
                    event_id=event_id,
                    event_type=payload.event,
                    body=body,
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
                base = websocket_base_url(app_settings)
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
        base = websocket_base_url(app_settings)
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
        media_session = new_media_session(call_id)
        media_buffer = AsyncBoundedMediaBuffer(
            app_settings.voice_max_buffered_frames,
            app_settings.voice_stale_frame_ms,
            reliability,
        )
        receiver = asyncio.create_task(pump_frames(websocket, media_buffer, lambda frame: frame))
        try:
            while True:
                buffered = await media_buffer.pop()
                if buffered is None:
                    raise WebSocketDisconnect
                result = await media_session.handle(buffered.payload)
                if result.missing:
                    await websocket.close(code=4404, reason="Call not found")
                    return
                if result.ended:
                    await websocket.send_json({"type": "ended", "call_id": call_id})
                    await websocket.close(code=1000)
                    return
                for output in result.outputs:
                    await websocket.send_json(serialize_output(output))
        except WebSocketDisconnect:
            await media_session.disconnect()
        finally:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
            await media_buffer.close()
            await session_admission.release()
            media_session.close()

    async def verified_twilio_form(request: Request, signature: str | None) -> dict[str, str]:
        if not app_settings.twilio_auth_token:
            raise HTTPException(status_code=503, detail="Twilio is not configured")
        form = await request.form()
        params = [(key, str(value)) for key, value in form.multi_items()]
        # Twilio signs the public URL it called, which differs from the
        # request URL behind a proxy or load balancer.
        url = app_settings.voice_public_base_url.rstrip("/") + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        if not signature or not twilio.verify_twilio_signature(app_settings.twilio_auth_token, url, params, signature):
            raise HTTPException(status_code=401, detail="Invalid Twilio signature")
        return dict(params)

    def twiml_response(document: str) -> Response:
        return Response(document, media_type="application/xml")

    @app.post("/v1/telephony/twilio/voice/{tenant_id}")
    async def twilio_voice(
        request: Request,
        tenant_id: str,
        flow_key: str = "after_hours_intake",
        region: str | None = None,
        x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    ):
        form = await verified_twilio_form(request, x_twilio_signature)
        call_sid = form.get("CallSid", "")
        if not call_sid:
            raise HTTPException(status_code=422, detail="CallSid is required")
        async with session_factory() as session:
            try:
                call, _ = await admit_call(
                    session,
                    tenant_id=tenant_id,
                    provider=twilio.PROVIDER,
                    provider_call_id=call_sid,
                    caller=form.get("From", "unknown"),
                    callee=form.get("To", "unknown"),
                    region=region or form.get("FromCountry", "unknown").lower(),
                    flow_key=flow_key,
                )
            except AdmissionError as exc:
                config = await session.scalar(
                    select(VoiceFlowConfig).where(
                        VoiceFlowConfig.tenant_id == tenant_id,
                        VoiceFlowConfig.flow_key == flow_key,
                    )
                )
                reliability.increment(f"twilio_admission_denied_{exc.code}_total")
                return twiml_response(twilio.unavailable_twiml(config.transfer_number if config else None))
            await store_raw_payload(
                session,
                app_settings,
                call,
                provider=twilio.PROVIDER,
                provider_call_id=call_sid,
                event_id=f"{call_sid}:incoming",
                event_type="incoming",
                body=json.dumps(form, sort_keys=True).encode(),
            )
            await session.commit()
        token = adapter.media_token(call.id, int(time.time()) + 3600)
        stream_url = f"{websocket_base_url(app_settings)}/v1/twilio/media/{call.id}"
        return twiml_response(twilio.connect_stream_twiml(stream_url, token))

    @app.post("/v1/telephony/twilio/status", status_code=204)
    async def twilio_status(
        request: Request,
        x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    ):
        form = await verified_twilio_form(request, x_twilio_signature)
        call_sid = form.get("CallSid", "")
        call_status = form.get("CallStatus", "")
        async with session_factory() as session:
            call = await session.scalar(
                select(VoiceCall).where(VoiceCall.provider == twilio.PROVIDER, VoiceCall.provider_call_id == call_sid)
            )
            if call is None:
                return Response(status_code=204)
            await store_raw_payload(
                session,
                app_settings,
                call,
                provider=twilio.PROVIDER,
                provider_call_id=call_sid,
                event_id=f"{call_sid}:{call_status}:{form.get('SequenceNumber', '')}",
                event_type=call_status or "status",
                body=json.dumps(form, sort_keys=True).encode(),
            )
            if call_status == "completed":
                await hangup_call(session, call, "provider_completed")
            elif call_status in twilio.TERMINAL_STATUSES:
                call.status = "failed"
                call.outcome = "provider_failure"
                await append_event(session, call, "call.failed", {"reason": call_status})
                await hangup_call(session, call, call_status)
            else:
                await append_event(session, call, "provider.status", {"status": call_status})
            await session.commit()
        return Response(status_code=204)

    async def run_call_control(call_id: str, call_sid: str, action: twilio.PendingAction) -> None:
        client: twilio.TwilioClient | None = app.state.twilio_client
        try:
            if client is None:
                raise RuntimeError("Twilio REST credentials are not configured")
            if action.kind == "transfer" and action.transfer_number:
                await client.transfer(call_sid, action.transfer_number)
            else:
                await client.hang_up(call_sid)
            event_type, payload = f"twilio.{action.kind}.requested", {}
        except (httpx.HTTPError, RuntimeError) as exc:
            reliability.increment("twilio_call_control_failures_total")
            event_type, payload = "twilio.call_control.failed", {"action": action.kind, "error": type(exc).__name__}
        async with session_factory() as session:
            call = await session.get(VoiceCall, call_id)
            if call is not None:
                await append_event(session, call, event_type, payload)
                await session.commit()

    @app.websocket("/v1/twilio/media/{call_id}")
    async def twilio_media_stream(websocket: WebSocket, call_id: str):
        if not await session_admission.acquire():
            await websocket.close(code=4429, reason="Gateway capacity reached; retry on failover")
            return
        try:
            await websocket.accept()
        except Exception:
            await session_admission.release()
            raise
        media_session: MediaSession | None = None
        receiver: asyncio.Task | None = None
        media_buffer = AsyncBoundedMediaBuffer(
            app_settings.voice_max_buffered_frames,
            app_settings.voice_stale_frame_ms,
            reliability,
        )
        try:
            # Twilio sends `connected`, then `start` carrying the custom parameters.
            start: dict = {}
            while start.get("event") != "start":
                start = json.loads(await websocket.receive_text())
                if start.get("event") not in {"connected", "start"}:
                    break
            details = start.get("start") or {}
            token = str((details.get("customParameters") or {}).get("token", ""))
            if start.get("event") != "start" or not adapter.verify_media_token(token, call_id):
                await websocket.close(code=4401, reason="Invalid media token")
                return
            stream_sid = str(start.get("streamSid") or details.get("streamSid", ""))
            call_sid = str(details.get("callSid", ""))
            media_session = new_media_session(call_id)
            pending: dict[int, twilio.PendingAction] = {}
            control_started = False

            async def control(action: twilio.PendingAction) -> None:
                # A call is transferred or hung up at most once; frames that
                # arrive afterwards must not issue more REST requests.
                nonlocal control_started
                if control_started:
                    return
                control_started = True
                await run_call_control(call_id, call_sid, action)

            async def deliver(result: FrameResult) -> None:
                for output in result.outputs:
                    for message in twilio.to_twilio_messages(output, stream_sid, end_call=result.call_finished):
                        await websocket.send_json(message)
                    if output.generation_id is None:
                        continue
                    if output.event_type == "transfer":
                        # Without a configured number the only safe end is a hang-up.
                        kind = "transfer" if output.transfer_number else "hang_up"
                        pending[output.generation_id] = twilio.PendingAction(kind, output.transfer_number)
                    elif result.call_finished:
                        pending[output.generation_id] = twilio.PendingAction("hang_up")
                if result.call_finished and not pending:
                    # Nothing left to play (or the goodbye mark already came back).
                    await control(twilio.PendingAction("hang_up"))

            result = await media_session.handle(
                {"type": "start", "codec": twilio.TWILIO_CODEC, "sample_rate_hz": twilio.TWILIO_SAMPLE_RATE_HZ}
            )
            if result.missing:
                await websocket.close(code=4404, reason="Call not found")
                return
            await deliver(result)
            receiver = asyncio.create_task(pump_frames(websocket, media_buffer, lambda message: message))
            while True:
                buffered = await media_buffer.pop()
                if buffered is None:
                    raise WebSocketDisconnect
                message = buffered.payload
                action = twilio.mark_action(message, pending)
                try:
                    frame = twilio.to_internal_frame(message)
                except ValueError:
                    reliability.increment("media_frames_invalid_total")
                    continue
                if frame is None:
                    continue
                result = await media_session.handle(frame)
                if result.missing:
                    await websocket.close(code=4404, reason="Call not found")
                    return
                if result.ended:
                    await websocket.close(code=1000)
                    return
                await deliver(result)
                if action is not None:
                    await control(action)
        except (WebSocketDisconnect, json.JSONDecodeError):
            if media_session is not None:
                await media_session.disconnect()
        finally:
            if receiver is not None:
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
            await media_buffer.close()
            await session_admission.release()
            if media_session is not None:
                media_session.close()

    return app


app = create_voice_app()


def main() -> None:
    import uvicorn

    uvicorn.run("services.voice.omni_voice.main:app", host="0.0.0.0", port=8002, reload=True)


if __name__ == "__main__":
    main()
