import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol


class StreamingSpeechToText(Protocol):
    async def transcribe(self, audio: bytes, metadata: dict[str, Any]) -> "TranscriptHypothesis | None": ...


class StreamingTextToSpeech(Protocol):
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

    Production adapters replace this class and consume the same audio/metadata
    interface. Transcript hints are never enabled by a hosted adapter.
    """

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
    async def synthesize(self, text: str) -> list[bytes]:
        encoded = text.encode("utf-8")
        return [encoded[index : index + 160] for index in range(0, len(encoded), 160)] or [b""]


class LocalRealtimeModel:
    async def generate(self, instruction: str, state: dict[str, Any]) -> str:
        del state
        return " ".join(instruction.split())[:500]


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
