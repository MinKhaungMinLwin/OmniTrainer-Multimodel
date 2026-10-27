import base64
import math
import xml.etree.ElementTree as ElementTree
from array import array
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app
from services.voice.omni_voice import twilio
from services.voice.omni_voice.audio import (
    UtteranceSegmenter,
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    pcm16_to_wav,
    resample_pcm16,
)
from services.voice.omni_voice.main import create_voice_app
from services.voice.omni_voice.providers import GeminiTTS, TranscriptHypothesis
from tests.test_voice_pilot import auth, enable

PUBLIC_BASE_URL = "https://voice.example.com"
AUTH_TOKEN = "twilio-test-auth-token"
ACCOUNT_SID = "AC00000000000000000000000000000000"


def tone(duration_ms: int, sample_rate_hz: int = 8000, amplitude: int = 4000) -> bytes:
    count = sample_rate_hz * duration_ms // 1000
    return array(
        "h", (int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate_hz)) for index in range(count))
    ).tobytes()


def silence(duration_ms: int, sample_rate_hz: int = 8000) -> bytes:
    return bytes(sample_rate_hz * duration_ms // 1000 * 2)


def frames(pcm: bytes, frame_ms: int = 20, sample_rate_hz: int = 8000) -> list[bytes]:
    size = sample_rate_hz * frame_ms // 1000 * 2
    return [pcm[index : index + size] for index in range(0, len(pcm), size)]


def test_mulaw_codec_round_trip_and_reference_values():
    assert pcm16_to_mulaw(array("h", [0]).tobytes()) == b"\xff"
    assert mulaw_to_pcm16(b"\xff") == array("h", [0]).tobytes()
    assert pcm16_to_mulaw(array("h", [32767, -32768]).tobytes()) == b"\x80\x00"
    source = array("h", range(-32000, 32000, 997))
    decoded = array("h", mulaw_to_pcm16(pcm16_to_mulaw(source.tobytes())))
    for original, restored in zip(source, decoded):
        # mu-law keeps relative error within a few percent of the magnitude.
        assert abs(original - restored) <= max(16, abs(original) * 0.04)


def test_resampling_preserves_duration():
    one_second_24k = tone(1000, sample_rate_hz=24000)
    assert len(resample_pcm16(one_second_24k, 24000, 8000)) == 8000 * 2
    assert len(resample_pcm16(tone(100), 8000, 16000)) == 1600 * 2
    assert resample_pcm16(b"", 8000, 16000) == b""


def test_segmenter_endpoints_speech_and_drops_noise_bursts():
    segmenter = UtteranceSegmenter(8000, threshold=500, min_speech_ms=200, end_silence_ms=700, max_utterance_ms=15000)
    events = []
    for frame in frames(silence(300) + tone(60) + silence(800)):
        events.extend(segmenter.push(frame))
    assert events == []

    for frame in frames(silence(300) + tone(500) + silence(800)):
        events.extend(segmenter.push(frame))
    assert [event.kind for event in events] == ["speech_start", "utterance"]
    utterance = events[1]
    # Pre-roll keeps the 200 ms before onset so leading consonants are not clipped.
    assert utterance.start_ms == 1160 + 300 - 200
    assert utterance.end_ms - utterance.start_ms == 200 + 500 + 700
    assert len(utterance.pcm) == (200 + 500 + 700) * 16


def test_segmenter_caps_long_utterances():
    segmenter = UtteranceSegmenter(8000, threshold=500, min_speech_ms=100, end_silence_ms=700, max_utterance_ms=1000)
    events = []
    for frame in frames(tone(2500)):
        events.extend(segmenter.push(frame))
    assert [event.kind for event in events].count("utterance") == 2


async def test_gemini_tts_encodes_telephony_frames(monkeypatch):
    tts = GeminiTTS(Settings(GEMINI_API_KEY="unit"), "mulaw", 8000, timeout_seconds=5)

    async def fake_synthesize(text: str, voice: str | None = None) -> bytes:
        return pcm16_to_wav(tone(1000, sample_rate_hz=24000), 24000)

    monkeypatch.setattr(tts.service, "synthesize", fake_synthesize)
    chunks = await tts.synthesize("Hello")
    assert len(chunks) == 50
    assert {len(chunk) for chunk in chunks} == {160}
    with pytest.raises(ValueError):
        GeminiTTS(Settings(GEMINI_API_KEY="unit"), "opus", 48000, timeout_seconds=5)


def test_twilio_signature_matches_reference_implementation():
    # Reference value computed with twilio.request_validator.RequestValidator.
    url = "https://voice.example.com/v1/telephony/twilio/voice/t1?region=local"
    params = {"CallSid": "CA123", "From": "+15550100", "To": "+15550999", "Digits": "1234", "AccountSid": "AC1"}
    assert twilio.twilio_signature("secret-token", url, params.items()) == "tnbrAVApmWEF0cpaWCwqYZxumoI="
    assert not twilio.verify_twilio_signature("secret-token", url, {**params, "To": "+1"}.items(), "x")


class ScriptedSTT:
    name = "scripted-stt"

    def __init__(self, replies: list[str | Exception]):
        self.replies = list(replies)
        self.utterances: list[bytes] = []

    async def transcribe_utterance(self, pcm16: bytes, sample_rate_hz: int) -> TranscriptHypothesis | None:
        assert sample_rate_hz == 8000
        self.utterances.append(pcm16)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return TranscriptHypothesis(text=reply, is_final=True, confidence_bps=9000)


class ToneTTS:
    name = "tone-tts"

    async def synthesize(self, text: str) -> list[bytes]:
        return [pcm16_to_mulaw(frame) for frame in frames(tone(60))]


def voice_settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{path}",
        jwt_secret="test-" * 8,
        allow_dev_auth=True,
        auto_create_schema=True,
        attachment_dir=str(path.parent / "recordings"),
        voice_webhook_secret="test-webhook-secret",
        voice_media_secret="test-media-secret-with-enough-entropy",
        voice_public_base_url=PUBLIC_BASE_URL,
        voice_max_buffered_frames=1000,
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token=AUTH_TOKEN,
    )


def signed_post(client: TestClient, path: str, form: dict[str, str]):
    signature = twilio.twilio_signature(AUTH_TOKEN, PUBLIC_BASE_URL + path, form.items())
    return client.post(path, data=form, headers={"X-Twilio-Signature": signature})


class TwilioHarness:
    def __init__(self, tmp_path: Path, stt: ScriptedSTT, **config_overrides):
        self.app_settings = voice_settings(tmp_path / "twilio.db")
        with TestClient(create_app(self.app_settings), raise_server_exceptions=True) as api:
            headers, self.tenant_id = auth(api)
            enable(api, headers, **config_overrides)
        self.rest_requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.rest_requests.append(request)
            return httpx.Response(200, json={"sid": "CA-test"})

        client = twilio.TwilioClient(ACCOUNT_SID, AUTH_TOKEN, transport=httpx.MockTransport(record))
        self.app = create_voice_app(self.app_settings, twilio_client=client)
        self.app.state.utterance_stt = stt
        self.app.state.tts_factory = lambda codec, sample_rate_hz: ToneTTS()

    def rest_fields(self) -> list[dict[str, list[str]]]:
        return [parse_qs(request.content.decode()) for request in self.rest_requests]

    def call_details(self, call_id: str) -> dict:
        with TestClient(create_app(self.app_settings), raise_server_exceptions=True) as api:
            headers, _ = auth(api)
            return api.get(f"/api/v1/voice/calls/{call_id}", headers=headers).json()


def admit(voice: TestClient, tenant_id: str, call_sid: str = "CA-test") -> tuple[str, str]:
    path = f"/v1/telephony/twilio/voice/{tenant_id}?region=local"
    response = signed_post(voice, path, {"CallSid": call_sid, "From": "+15550400", "To": "+15550999"})
    assert response.status_code == 200, response.text
    stream = ElementTree.fromstring(response.text).find("./Connect/Stream")
    url = stream.attrib["url"]
    assert url.startswith("wss://voice.example.com/v1/twilio/media/")
    token = stream.find("./Parameter[@name='token']").attrib["value"]
    return url.rsplit("/", 1)[1], token


def start_stream(websocket, token: str, call_sid: str = "CA-test") -> None:
    websocket.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    websocket.send_json(
        {
            "event": "start",
            "streamSid": "MZ-test",
            "start": {
                "streamSid": "MZ-test",
                "callSid": call_sid,
                "customParameters": {"token": token},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )


def speak(websocket, speech_ms: int = 400) -> None:
    for frame in frames(tone(speech_ms) + silence(800)):
        websocket.send_json(
            {
                "event": "media",
                "streamSid": "MZ-test",
                "media": {"track": "inbound", "payload": base64.b64encode(pcm16_to_mulaw(frame)).decode()},
            }
        )


def receive_until_mark(websocket) -> tuple[list[dict], str]:
    messages = []
    while True:
        message = websocket.receive_json()
        messages.append(message)
        if message["event"] == "mark":
            return messages, message["mark"]["name"]


def stop_stream(websocket) -> None:
    # The gateway closes the socket only after processing every queued frame.
    websocket.send_json({"event": "stop", "streamSid": "MZ-test"})
    with pytest.raises(WebSocketDisconnect):
        websocket.receive_json()


def echo_mark(websocket, name: str) -> None:
    websocket.send_json({"event": "mark", "streamSid": "MZ-test", "mark": {"name": name}})


def test_twilio_call_completes_intake_and_hangs_up_after_goodbye(tmp_path: Path):
    stt = ScriptedSTT(["Yes, I agree.", "Dana Lee", "My boiler is making a loud noise.", "Yes please."])
    harness = TwilioHarness(tmp_path, stt)
    with TestClient(harness.app, raise_server_exceptions=True) as voice:
        unsigned = voice.post(f"/v1/telephony/twilio/voice/{harness.tenant_id}", data={"CallSid": "CA-test"})
        assert unsigned.status_code == 401
        call_id, token = admit(voice, harness.tenant_id)
        assert admit(voice, harness.tenant_id)[0] == call_id

        with voice.websocket_connect(f"/v1/twilio/media/{call_id}") as websocket:
            start_stream(websocket, token)
            greeting, mark = receive_until_mark(websocket)
            assert [message["event"] for message in greeting] == ["media", "media", "media", "mark"]
            assert mark.startswith("gen-")

            # Caller talks over the greeting: playback is cleared (barge-in).
            speak(websocket)
            interrupted = websocket.receive_json()
            assert interrupted == {"event": "clear", "streamSid": "MZ-test"}
            _, mark = receive_until_mark(websocket)
            echo_mark(websocket, mark)

            for _ in range(2):
                speak(websocket)
                _, mark = receive_until_mark(websocket)
                echo_mark(websocket, mark)

            speak(websocket)
            _, mark = receive_until_mark(websocket)
            assert mark.startswith("hangup-")
            assert harness.rest_requests == []
            echo_mark(websocket, mark)
            stop_stream(websocket)

    assert len(stt.utterances) == 4
    assert harness.rest_fields() == [{"Status": ["completed"]}]
    assert harness.rest_requests[0].url.path == f"/2010-04-01/Accounts/{ACCOUNT_SID}/Calls/CA-test.json"
    call = harness.call_details(call_id)
    assert call["outcome"] == "callback_review_pending"
    assert call["review"]["proposed_args"]["name"] == "Dana Lee"
    assert call["job_id"] is None
    caller_segments = [segment for segment in call["transcript"] if segment["speaker"] == "caller"]
    assert {segment["provider"] for segment in caller_segments} == {"scripted-stt"}
    assert {segment["provider"] for segment in call["transcript"] if segment["speaker"] == "assistant"} == {"tone-tts"}
    event_types = [event["event_type"] for event in call["events"]]
    assert "playback.interrupted" in event_types
    assert "twilio.hang_up.requested" in event_types
    assert call["recording"]["codec"] == "mulaw"


def test_twilio_emergency_transfers_after_handoff_line(tmp_path: Path):
    harness = TwilioHarness(tmp_path, ScriptedSTT(["There is a gas leak in the kitchen"]))
    with TestClient(harness.app, raise_server_exceptions=True) as voice:
        call_id, token = admit(voice, harness.tenant_id)
        with voice.websocket_connect(f"/v1/twilio/media/{call_id}") as websocket:
            start_stream(websocket, token)
            _, mark = receive_until_mark(websocket)
            echo_mark(websocket, mark)
            speak(websocket)
            _, mark = receive_until_mark(websocket)
            assert mark.startswith("transfer-")
            echo_mark(websocket, mark)
            stop_stream(websocket)

    assert harness.rest_fields() == [
        {"Twiml": ['<?xml version="1.0" encoding="UTF-8"?><Response><Dial>+15550000</Dial></Response>']}
    ]
    call = harness.call_details(call_id)
    assert call["transfer_reason"] == "emergency_keyword"
    assert call["status"] == "transferred"


def test_twilio_stt_failures_reprompt_then_transfer(tmp_path: Path):
    harness = TwilioHarness(tmp_path, ScriptedSTT([RuntimeError("provider down"), RuntimeError("provider down")]))
    with TestClient(harness.app, raise_server_exceptions=True) as voice:
        call_id, token = admit(voice, harness.tenant_id)
        with voice.websocket_connect(f"/v1/twilio/media/{call_id}") as websocket:
            start_stream(websocket, token)
            _, mark = receive_until_mark(websocket)
            echo_mark(websocket, mark)
            speak(websocket)
            _, mark = receive_until_mark(websocket)
            assert mark.startswith("gen-")
            echo_mark(websocket, mark)
            speak(websocket)
            _, mark = receive_until_mark(websocket)
            assert mark.startswith("transfer-")

    call = harness.call_details(call_id)
    assert call["transfer_reason"] == "repeated_silence"
    fallbacks = [event for event in call["events"] if event["event_type"] == "provider.fallback.activated"]
    assert [event["payload"]["stage"] for event in fallbacks] == ["stt", "stt"]


def test_twilio_rejects_bad_media_token_and_denied_admission(tmp_path: Path):
    harness = TwilioHarness(tmp_path, ScriptedSTT([]), allowed_regions=["us-east"])
    with TestClient(harness.app, raise_server_exceptions=True) as voice:
        denied = signed_post(
            voice,
            f"/v1/telephony/twilio/voice/{harness.tenant_id}?region=local",
            {"CallSid": "CA-denied", "From": "+15550400", "To": "+15550999"},
        )
        assert denied.status_code == 200
        response = ElementTree.fromstring(denied.text)
        assert response.find("./Dial").text == "+15550000"
        assert response.find("./Connect") is None

        path = f"/v1/telephony/twilio/voice/{harness.tenant_id}?region=us-east"
        admitted = signed_post(voice, path, {"CallSid": "CA-ok", "From": "+15550400", "To": "+15550999"})
        call_id = ElementTree.fromstring(admitted.text).find("./Connect/Stream").attrib["url"].rsplit("/", 1)[1]
        with voice.websocket_connect(f"/v1/twilio/media/{call_id}") as websocket:
            start_stream(websocket, "forged-token", call_sid="CA-ok")
            with pytest.raises(Exception):
                websocket.receive_json()


def test_twilio_status_callback_closes_call_and_requires_configuration(tmp_path: Path):
    harness = TwilioHarness(tmp_path, ScriptedSTT([]))
    with TestClient(harness.app, raise_server_exceptions=True) as voice:
        call_id, _ = admit(voice, harness.tenant_id)
        status = signed_post(
            voice,
            "/v1/telephony/twilio/status",
            {"CallSid": "CA-test", "CallStatus": "no-answer", "SequenceNumber": "1"},
        )
        assert status.status_code == 204
        unknown = signed_post(voice, "/v1/telephony/twilio/status", {"CallSid": "CA-other", "CallStatus": "completed"})
        assert unknown.status_code == 204
        health = voice.get("/health/providers").json()
        assert health["stt"]["provider"] == "scripted-stt"
        assert health["telephony"]["twilio"] == "configured"

    call = harness.call_details(call_id)
    assert call["status"] == "failed"
    assert call["outcome"] == "provider_failure"

    unconfigured = voice_settings(tmp_path / "unconfigured.db").model_copy(update={"twilio_auth_token": None})
    with TestClient(create_voice_app(unconfigured), raise_server_exceptions=True) as voice:
        response = voice.post("/v1/telephony/twilio/voice/tenant", data={"CallSid": "CA-x"})
        assert response.status_code == 503
