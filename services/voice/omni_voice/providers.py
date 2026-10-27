import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from services.api.omni_api.config import Settings
from services.api.omni_api.gemini_audio import EmptyTranscriptionError, GeminiAudioService
from services.voice.omni_voice.audio import (
    MULAW_CODECS,
    PCM_CODECS,
    chunk_audio,
    encode_from_pcm16,
    pcm16_to_wav,
    resample_pcm16,
    wav_to_pcm16,
)


class StreamingSpeechToText(Protocol):
    async def transcribe(self, audio: bytes, metadata: dict[str, Any]) -> "TranscriptHypothesis | None": ...


class UtteranceSpeechToText(Protocol):
    """A hosted recognizer that receives one endpointed caller utterance at a time."""

    name: str

    async def transcribe_utterance(self, pcm16: bytes, sample_rate_hz: int) -> "TranscriptHypothesis | None": ...


class StreamingTextToSpeech(Protocol):
    name: str

    async def synthesize(self, text: str) -> list[bytes]: ...


class RealtimeModel(Protocol):
    async def generate(self, instruction: str, state: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class TranscriptHypothesis:
    text: str
    is_final: bool
    confidence_bps: int


class LocalStreamingSTT:
    """Deterministic adapter for simulations and provider contract tests.

    Hosted recognizers implement `UtteranceSpeechToText` instead and receive
    endpointed audio. Transcript hints are never honored by a hosted adapter.
    """

    name = "local-stt"

    async def transcribe(self, audio: bytes, metadata: dict[str, Any]) -> TranscriptHypothesis | None:
        hint = metadata.get("transcript_hint")
        if not hint:
            return None
        return TranscriptHypothesis(
            text=str(hint),
            is_final=bool(metadata.get("is_final", True)),
            confidence_bps=int(metadata.get("confidence_bps", 9900)),
        )


class LocalStreamingTTS:
    name = "local-tts"

    async def synthesize(self, text: str) -> list[bytes]:
        encoded = text.encode("utf-8")
        return [encoded[index : index + 160] for index in range(0, len(encoded), 160)] or [b""]


class LocalRealtimeModel:
    async def generate(self, instruction: str, state: dict[str, Any]) -> str:
        del state
        return " ".join(instruction.split())[:500]


class GeminiUtteranceSTT:
    """Transcribe endpointed caller utterances with Gemini.

    Gemini audio understanding is request/response rather than streaming, so the
    media session segments speech first and sends each utterance as a WAV file.
    Gemini returns no confidence score; `NOMINAL_CONFIDENCE_BPS` marks results
    as hosted-model transcripts without implying a measured probability.
    """

    name = "gemini-stt"
    NOMINAL_CONFIDENCE_BPS = 9000

    def __init__(self, settings: Settings):
        self.service = GeminiAudioService(settings)

    async def transcribe_utterance(self, pcm16: bytes, sample_rate_hz: int) -> TranscriptHypothesis | None:
        try:
            result = await self.service.transcribe(pcm16_to_wav(pcm16, sample_rate_hz), "audio/wav")
        except EmptyTranscriptionError:
            return None
        return TranscriptHypothesis(text=result.text, is_final=True, confidence_bps=self.NOMINAL_CONFIDENCE_BPS)


class GeminiTTS:
    """Synthesize agent speech with Gemini and encode it for the call's media codec."""

    name = "gemini-tts"

    def __init__(self, settings: Settings, codec: str, sample_rate_hz: int, timeout_seconds: float):
        normalized = codec.lower()
        if normalized not in MULAW_CODECS | PCM_CODECS:
            raise ValueError(f"Gemini TTS cannot encode {codec} audio")
        self.service = GeminiAudioService(settings)
        self.codec = normalized
        self.sample_rate_hz = sample_rate_hz
        self.timeout_seconds = timeout_seconds
        bytes_per_sample = 1 if normalized in MULAW_CODECS else 2
        # 20 ms frames match the telephony packetization interval.
        self.bytes_per_chunk = sample_rate_hz * bytes_per_sample // 50

    async def synthesize(self, text: str) -> list[bytes]:
        wav = await asyncio.wait_for(self.service.synthesize(text), timeout=self.timeout_seconds)
        pcm, source_rate_hz = wav_to_pcm16(wav)
        encoded = encode_from_pcm16(resample_pcm16(pcm, source_rate_hz, self.sample_rate_hz), self.codec)
        return chunk_audio(encoded, self.bytes_per_chunk)


def build_utterance_stt(settings: Settings) -> UtteranceSpeechToText | None:
    """Return the hosted recognizer, or None to keep the deterministic per-frame contract."""

    if settings.voice_stt_provider == "gemini":
        return GeminiUtteranceSTT(settings)
    return None


def build_tts(settings: Settings, codec: str, sample_rate_hz: int) -> StreamingTextToSpeech:
    if settings.voice_tts_provider == "gemini":
        return GeminiTTS(settings, codec, sample_rate_hz, settings.voice_hosted_tts_timeout_ms / 1000)
    return LocalStreamingTTS()


def normalize_audio(audio: bytes, codec: str, sample_rate_hz: int, target_rate_hz: int = 16_000) -> bytes:
    """Normalize signed PCM16 audio for STT adapters; encoded telephony frames pass through.

    Provider STT adapters may natively consume PCMU, PCMA, or Opus. PCM frames
    are resampled here with deterministic nearest-neighbor selection so the
    gateway contract can be exercised without platform codec dependencies.
    """

    if codec.lower() not in {"pcm16", "pcm16le", "l16"} or sample_rate_hz == target_rate_hz:
        return audio
    if sample_rate_hz <= 0 or len(audio) < 2:
        return b""
    samples = [audio[index : index + 2] for index in range(0, len(audio) - 1, 2)]
    target_count = max(1, round(len(samples) * target_rate_hz / sample_rate_hz))
    return b"".join(
        samples[min(len(samples) - 1, index * sample_rate_hz // target_rate_hz)] for index in range(target_count)
    )


class GenericTelephonyAdapter:
    def __init__(self, webhook_secret: str, media_secret: str, tolerance_seconds: int = 300):
        self.webhook_secret = webhook_secret.encode()
        self.media_secret = media_secret.encode()
        self.tolerance_seconds = tolerance_seconds

    def sign_webhook(self, body: bytes, timestamp: int) -> str:
        return hmac.new(self.webhook_secret, str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()

    def verify_webhook(self, body: bytes, timestamp: str, signature: str) -> bool:
        try:
            parsed_timestamp = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - parsed_timestamp) > self.tolerance_seconds:
            return False
        expected = self.sign_webhook(body, parsed_timestamp)
        return hmac.compare_digest(expected, signature)

    def media_token(self, call_id: str, expires_at: int) -> str:
        payload = json.dumps({"call_id": call_id, "exp": expires_at}, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self.media_secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def verify_media_token(self, token: str, call_id: str) -> bool:
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.media_secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return False
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            return payload["call_id"] == call_id and int(payload["exp"]) >= int(time.time())
        except (ValueError, KeyError, json.JSONDecodeError):
            return False
