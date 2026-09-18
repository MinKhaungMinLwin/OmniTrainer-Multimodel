import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app
from services.voice.omni_voice.main import create_voice_app
from services.voice.omni_voice.providers import GenericTelephonyAdapter, normalize_audio


def settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{path}",
        jwt_secret="test-" * 8,
        allow_dev_auth=True,
        auto_create_schema=True,
        attachment_dir=str(path.parent / "recordings"),
        voice_webhook_secret="test-webhook-secret",
        voice_media_secret="test-media-secret-with-enough-entropy",
    )


def auth(client: TestClient) -> tuple[dict[str, str], str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    bearer = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=bearer).json()[0]["id"]
    return {**bearer, "X-Tenant-ID": tenant_id}, tenant_id


def enable(client: TestClient, headers: dict[str, str], **overrides):
    payload = {
        "enabled": True,
        "pilot_mode": "internal",
        "allowed_hours": {
            "timezone": "UTC",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "start": "00:00",
            "end": "23:59",
        },
        "max_concurrent_calls": 4,
        "allowed_regions": ["local", "us-east"],
        "disclosure_text": "Hello. I am an AI assistant. This call may be recorded. Do you consent to continue?",
        "require_ai_consent": True,
        "require_recording_consent": True,
        "retention_days": 30,
        "transfer_number": "+15550000",
        "emergency_keywords": ["fire", "gas leak", "medical emergency"],
        "allowed_tools": ["find_customer", "create_callback_job"],
        "prompt_version": "after-hours-v1",
        **overrides,
    }
    response = client.put("/api/v1/voice/config", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_reviewed_after_hours_intake_creates_no_unsupervised_job(tmp_path: Path):
    app_settings = settings(tmp_path / "voice.db")
    with TestClient(create_app(app_settings), raise_server_exceptions=True) as client:
        headers, _ = auth(client)
        disabled = client.post(
            "/api/v1/voice/simulations",
            headers=headers,
            json={"turns": [{"text": "yes"}]},
        )
        assert disabled.status_code == 409
        enable(client, headers)
        simulation = client.post(
            "/api/v1/voice/simulations",
            headers=headers,
            json={
                "caller": "+15550100",
                "turns": [
                    {"text": "yes"},
                    {"text": "José Álvarez"},
                    {"text": "The kitchen sink is leaking"},
                    {"text": "yes"},
                ],
            },
        )
        assert simulation.status_code == 200, simulation.text
        call = simulation.json()
        assert call["outcome"] == "callback_review_pending"
        assert call["review"]["status"] == "pending"
        assert call["job_id"] is None
        assert call["recording"]["size_bytes"] > 0
        assert any(event["event_type"] == "consent.granted" for event in call["events"])
        assert any(tool["name"] == "find_customer" for tool in call["tools"])
        assert {segment["speaker"] for segment in call["transcript"]} == {"caller", "assistant"}
        assert call["review"]["proposed_args"]["name"] == "José Álvarez"

        args = call["review"]["proposed_args"]
        args["title"] = "Reviewed voice callback"
        approved = client.post(
            f"/api/v1/voice/calls/{call['id']}/review",
            headers=headers,
            json={"decision": "approve", "args": args, "reason": "Dispatcher verified"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["outcome"] == "callback_job_created"
        assert approved.json()["job_id"] is not None
        jobs = client.get("/api/v1/jobs", headers=headers).json()["items"]
        assert sum(job["title"] == "Reviewed voice callback" for job in jobs) == 1

        denied = client.post(
            "/api/v1/voice/simulations",
            headers=headers,
            json={"region": "outside-pilot", "turns": [{"text": "yes"}]},
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "region_denied"


def test_emergency_consent_dtmf_silence_and_voicemail_fallbacks(tmp_path: Path):
    app_settings = settings(tmp_path / "fallbacks.db")
    with TestClient(create_app(app_settings), raise_server_exceptions=True) as client:
        headers, _ = auth(client)
        enable(client, headers)
        scenarios = [
            ([{"text": "There is a gas leak"}], "human_transfer", "emergency_keyword"),
            ([{"text": "no"}], "human_transfer", "consent_declined"),
            ([{"type": "dtmf", "digit": "0"}], "human_transfer", "dtmf_zero"),
            ([{"type": "silence"}, {"type": "silence"}], "human_transfer", "repeated_silence"),
            ([{"type": "voicemail"}], "voicemail", None),
        ]
        for index, (turns, outcome, reason) in enumerate(scenarios):
            response = client.post(
                "/api/v1/voice/simulations",
                headers=headers,
                json={"caller": f"+1555020{index}", "turns": turns},
            )
            assert response.status_code == 200, response.text
            call = response.json()
            assert call["outcome"] == outcome
            if reason:
                assert call["transfer_reason"] == reason
            assert call["review"] is None


def test_signed_webhook_streaming_barge_in_and_recording_alignment(tmp_path: Path):
    database = tmp_path / "gateway.db"
    app_settings = settings(database)
    with TestClient(create_app(app_settings), raise_server_exceptions=True) as api:
        headers, tenant_id = auth(api)
        enable(api, headers)

    adapter = GenericTelephonyAdapter(
        app_settings.voice_webhook_secret,
        app_settings.voice_media_secret,
        app_settings.voice_webhook_tolerance_seconds,
    )
    incoming = {
        "event": "incoming",
        "tenant_id": tenant_id,
        "provider": "generic",
        "provider_call_id": "provider-call-1",
        "caller": "+15550300",
        "callee": "+15550999",
        "region": "local",
        "flow_key": "after_hours_intake",
    }
    body = json.dumps(incoming, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signature = adapter.sign_webhook(body, timestamp)
    with TestClient(create_voice_app(app_settings), raise_server_exceptions=True) as voice:
        stale = voice.post(
            "/v1/webhooks/telephony",
            content=body,
            headers={
                "X-Voice-Timestamp": str(timestamp - 1000),
                "X-Voice-Signature": adapter.sign_webhook(body, timestamp - 1000),
            },
        )
        assert stale.status_code == 401
        invalid = voice.post(
            "/v1/webhooks/telephony",
            content=body,
            headers={"X-Voice-Timestamp": str(timestamp), "X-Voice-Signature": "invalid"},
        )
        assert invalid.status_code == 401
        admitted = voice.post(
            "/v1/webhooks/telephony",
            content=body,
            headers={"X-Voice-Timestamp": str(timestamp), "X-Voice-Signature": signature},
        )
        assert admitted.status_code == 200, admitted.text
        admission = admitted.json()
        duplicate = voice.post(
            "/v1/webhooks/telephony",
            content=body,
            headers={"X-Voice-Timestamp": str(timestamp), "X-Voice-Signature": signature},
        )
        assert duplicate.json()["call_id"] == admission["call_id"]
        assert duplicate.json()["created"] is False

        token = parse_qs(urlparse(admission["media_url"]).query)["token"][0]
        offer = voice.post(
            "/v1/webrtc/offer",
            json={"call_id": admission["call_id"], "token": token, "sdp": "v=0\r\n"},
        )
        assert offer.status_code == 200, offer.text
        assert "PCMU/8000" in offer.json()["codecs"]

        media_path = admission["media_url"].split("localhost:8002", 1)[1]
        with voice.websocket_connect(media_path) as websocket:
            websocket.send_json({"type": "start", "codec": "mulaw", "sample_rate_hz": 8000})
            greeting = websocket.receive_json()
            assert greeting["type"] == "playback"
            websocket.send_json({"type": "speech_start", "heard_response_boundary_ms": 240})
            interrupted = websocket.receive_json()
            assert interrupted["type"] == "clear_playback"
            websocket.send_json(
                {
                    "type": "audio",
                    "audio": base64.b64encode(b"noisy-audio-frame").decode(),
                    "duration_ms": 20,
                    "transcript_hint": "yes",
                    "is_final": True,
                    "confidence_bps": 8700,
                }
            )
            assert "What is your name" in websocket.receive_json()["text"]
            websocket.send_json({"type": "hangup"})
            assert websocket.receive_json()["type"] == "ended"

    with TestClient(create_app(app_settings), raise_server_exceptions=True) as api:
        headers, _ = auth(api)
        call = api.get(f"/api/v1/voice/calls/{admission['call_id']}", headers=headers).json()
        event_types = [event["event_type"] for event in call["events"]]
        assert "playback.interrupted" in event_types
        assert "recording.stored" in event_types
        assert call["recording"]["size_bytes"] == len(b"noisy-audio-frame")
        caller_segment = next(segment for segment in call["transcript"] if segment["speaker"] == "caller")
        assert caller_segment["event_sequence"] in {event["sequence"] for event in call["events"]}
        metrics = api.get("/api/v1/voice/metrics", headers=headers).json()
        assert metrics["total_calls"] == 1
        assert metrics["average_first_audio_ms"] <= metrics["target_first_audio_ms"]
        raw_payloads = list((tmp_path / "recordings" / "call-data" / "raw").rglob("*.json"))
        assert len(raw_payloads) == 1
        assert json.loads(raw_payloads[0].read_text())["provider_call_id"] == "provider-call-1"


def test_pcm_audio_resampling_contract():
    source = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    assert normalize_audio(source, "pcm16le", 8000, 16000) == (
        b"\x01\x00\x01\x00\x02\x00\x02\x00\x03\x00\x03\x00\x04\x00\x04\x00"
    )
    assert normalize_audio(source, "mulaw", 8000, 16000) == source
