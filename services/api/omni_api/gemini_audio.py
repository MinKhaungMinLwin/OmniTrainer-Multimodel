import asyncio
import io
import wave
from dataclasses import dataclass

from google import genai
from google.genai import types
from openinference.semconv.trace import OpenInferenceSpanKindValues

from services.api.omni_api.config import Settings
from services.observability import traced_span

SUPPORTED_AUDIO_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
}


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiAudioError(RuntimeError):
    pass


def gemini_client(settings: Settings) -> genai.Client:
    if not settings.gemini_api_key:
        raise GeminiConfigurationError("Gemini is not configured")
    http_options = None
    if settings.gemini_base_url:
        http_options = types.HttpOptions(base_url=settings.gemini_base_url.rstrip("/"))
    return genai.Client(api_key=settings.gemini_api_key, http_options=http_options)


def pcm_to_wav(pcm: bytes, sample_rate: int = 24_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm)
    return output.getvalue()


@dataclass(frozen=True)
class Transcription:
    text: str
    model: str


class GeminiAudioService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcription:
        with traced_span(
            "audio.transcribe",
            OpenInferenceSpanKindValues.CHAIN,
            attributes={
                "audio.input_bytes": len(audio),
                "audio.mime_type": mime_type,
                "audio.model": self.settings.gemini_stt_model,
            },
        ) as span:
            result = await self._transcribe(audio, mime_type)
            span.set_attribute("audio.transcript_characters", len(result.text))
            return result

    async def _transcribe(self, audio: bytes, mime_type: str) -> Transcription:
        client = gemini_client(self.settings)

        def request():
            return client.models.generate_content(
                model=self.settings.gemini_stt_model,
                contents=[
                    "Transcribe this speech accurately. Return only the spoken words with natural punctuation.",
                    types.Part.from_bytes(data=audio, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(temperature=0),
            )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(request),
                timeout=self.settings.ai_timeout_seconds,
            )
        except Exception as exc:
            raise GeminiAudioError("Gemini could not transcribe this recording") from exc
        text = (response.text or "").strip()
        if not text:
            raise GeminiAudioError("Gemini returned an empty transcription")
        return Transcription(text=text, model=self.settings.gemini_stt_model)

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        selected_voice = voice or self.settings.gemini_tts_voice
        with traced_span(
            "audio.synthesize",
            OpenInferenceSpanKindValues.CHAIN,
            attributes={
                "audio.input_characters": len(text),
                "audio.voice": selected_voice,
                "audio.model": self.settings.gemini_tts_model,
            },
        ) as span:
            audio = await self._synthesize(text, selected_voice)
            span.set_attribute("audio.output_bytes", len(audio))
            return audio

    async def _synthesize(self, text: str, selected_voice: str) -> bytes:
        client = gemini_client(self.settings)

        def request():
            return client.models.generate_content(
                model=self.settings.gemini_tts_model,
                contents=f"Speak clearly and naturally: {text}",
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=selected_voice)
                        )
                    ),
                ),
            )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(request),
                timeout=self.settings.ai_timeout_seconds,
            )
            parts = response.candidates[0].content.parts
            pcm = next(part.inline_data.data for part in parts if part.inline_data and part.inline_data.data)
        except Exception as exc:
            raise GeminiAudioError("Gemini could not synthesize this response") from exc
        return pcm_to_wav(pcm)
