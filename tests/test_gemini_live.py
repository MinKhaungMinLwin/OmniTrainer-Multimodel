import pytest
from dotenv import dotenv_values

from services.api.omni_api.ai_gateway import GeminiCopilotProvider
from services.api.omni_api.config import Settings
from services.api.omni_api.gemini_audio import GeminiAudioService


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_gemini_chat_tts_stt_round_trip():
    environment = dotenv_values(".env")
    api_key = environment.get("GEMINI_API_KEY")
    if not api_key or api_key.startswith("unit-test"):
        pytest.skip("A real GEMINI_API_KEY is required")
    settings = Settings(
        GEMINI_API_KEY=api_key,
        GOOGLE_GEMINI_BASE_URL=environment.get("GOOGLE_GEMINI_BASE_URL") or None,
        ai_provider="gemini",
        ai_model=environment.get("OMNI_AI_MODEL") or "gemini-3.5-flash-lite",
        ai_timeout_seconds=60,
    )

    plan = GeminiCopilotProvider(
        settings.gemini_api_key,
        settings.ai_model,
        settings.gemini_base_url,
    ).plan("Reply with one short greeting and do not use a tool.")
    assert plan.provider == "gemini"
    assert not plan.tools
    assert plan.introduction

    service = GeminiAudioService(settings)
    speech = await service.synthesize("Omni voice testing is ready.")
    assert speech.startswith(b"RIFF")
    transcript = await service.transcribe(speech, "audio/wav")
    normalized = transcript.text.lower().replace(" ", "")
    assert "omni" in normalized
    assert "testingisready" in normalized
