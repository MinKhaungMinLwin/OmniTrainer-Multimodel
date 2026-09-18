from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.api.omni_api.ai_gateway import GeminiCopilotProvider, GeminiPlan, GeminiToolPlan
from services.api.omni_api.config import Settings
from services.api.omni_api.gemini_audio import GeminiAudioService, Transcription, pcm_to_wav
from services.api.omni_api.main import create_app


def test_gemini_provider_returns_only_typed_allowlisted_tools():
    provider = GeminiCopilotProvider("test-key", "gemini-test")
    provider.client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_: SimpleNamespace(
                parsed=GeminiPlan(
                    response="I’ll find that customer.",
                    tools=[GeminiToolPlan(name="find_customer", arguments_json='{"query":"Northwind"}')],
                ),
                text="",
                usage_metadata=SimpleNamespace(prompt_token_count=12),
            )
        )
    )
    result = provider.plan("Find customer Northwind")
    assert result.provider == "gemini"
    assert result.model == "gemini-test"
    assert result.tools[0].name == "find_customer"
    assert result.tools[0].arguments == {"query": "Northwind"}


def test_pcm_is_wrapped_as_browser_playable_wav():
    audio = pcm_to_wav(b"\x00\x00" * 240)
    assert audio.startswith(b"RIFF")
    assert b"WAVE" in audio[:16]


def build_app(database_path: Path):
    return create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path}",
            jwt_secret="test-" * 8,
            allow_dev_auth=True,
            auto_create_schema=True,
            storage_backend="local",
            gemini_api_key="test-key",
        )
    )


def authenticate(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=auth).json()[0]["id"]
    return {**auth, "X-Tenant-ID": tenant_id}


def test_authenticated_chat_audio_endpoints(monkeypatch, tmp_path: Path):
    async def transcribe(_self, audio: bytes, mime_type: str):
        assert audio == b"webm-audio"
        assert mime_type == "audio/webm"
        return Transcription(text="Find customer Northwind", model="gemini-stt-test")

    async def synthesize(_self, text: str, voice: str | None = None):
        assert text == "Northwind is ready."
        assert voice is None
        return pcm_to_wav(b"\x00\x00" * 240)

    monkeypatch.setattr(GeminiAudioService, "transcribe", transcribe)
    monkeypatch.setattr(GeminiAudioService, "synthesize", synthesize)
    with TestClient(build_app(tmp_path / "gemini-audio.db")) as client:
        headers = authenticate(client)
        transcription = client.post(
            "/api/v1/ai/audio/transcriptions",
            headers=headers,
            files={"file": ("recording.webm", b"webm-audio", "audio/webm;codecs=opus")},
        )
        assert transcription.status_code == 200
        assert transcription.json()["text"] == "Find customer Northwind"

        speech = client.post(
            "/api/v1/ai/audio/speech",
            headers=headers,
            json={"text": "Northwind is ready."},
        )
        assert speech.status_code == 200
        assert speech.headers["content-type"].startswith("audio/wav")
        assert speech.content.startswith(b"RIFF")

        unsupported = client.post(
            "/api/v1/ai/audio/transcriptions",
            headers=headers,
            files={"file": ("recording.txt", b"not audio", "text/plain")},
        )
        assert unsupported.status_code == 415
