"""Transport-independent media session shared by the generic and Twilio gateways."""

import base64
import time
from collections.abc import Callable
from contextvars import Token
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.api.omni_api.config import Settings
from services.api.omni_api.models import VoiceCall
from services.voice.omni_voice.audio import SegmentEvent, UtteranceSegmenter, decode_to_pcm16
from services.voice.omni_voice.providers import (
    LocalStreamingSTT,
    LocalStreamingTTS,
    StreamingTextToSpeech,
    TranscriptHypothesis,
    UtteranceSpeechToText,
    normalize_audio,
)
from services.voice.omni_voice.reliability import CircuitBreaker, ReliabilityMetrics, ReliableProvider
from services.voice.omni_voice.runtime import (
    VoiceOutput,
    answer_call,
    append_event,
    append_transcript,
    handle_dtmf,
    handle_silence,
    handle_user_turn,
    handle_voicemail,
    hangup_call,
    reset_voice_tts,
    speech_started,
    store_recording,
    use_voice_tts,
)

TtsFactory = Callable[[str, int], StreamingTextToSpeech]


@dataclass
class FrameResult:
    outputs: list[VoiceOutput] = field(default_factory=list)
    ended: bool = False
    missing: bool = False
    # The flow reached a terminal state (for example after the goodbye line),
    # so the transport should end the call once playback finishes.
    call_finished: bool = False


async def _no_transcript() -> TranscriptHypothesis | None:
    return None


class MediaSession:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        call_id: str,
        reliability: ReliabilityMetrics,
        stt_breaker: CircuitBreaker,
        utterance_stt: UtteranceSpeechToText | None,
        tts_factory: TtsFactory,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.call_id = call_id
        self.reliability = reliability
        self.stt_breaker = stt_breaker
        self.utterance_stt = utterance_stt
        self.tts_factory = tts_factory
        self.codec = "mulaw"
        self.sample_rate_hz = 8000
        self.duration_ms = 0
        self.audio_buffer = bytearray()
        self.stt = LocalStreamingSTT()
        self.fallback_stt = LocalStreamingSTT()
        self.segmenter: UtteranceSegmenter | None = None
        self._tts_token: Token | None = None

    async def handle(self, frame: dict) -> FrameResult:
        async with self.session_factory() as session:
            call = await session.get(VoiceCall, self.call_id)
            if call is None:
                return FrameResult(missing=True)
            result = FrameResult()
            frame_type = frame.get("type")
            output: VoiceOutput | None = None
            if frame_type == "start":
                self.codec = str(frame.get("codec", "mulaw"))
                self.sample_rate_hz = int(frame.get("sample_rate_hz", 8000))
                await append_event(
                    session, call, "media.started", {"codec": self.codec, "sample_rate_hz": self.sample_rate_hz}
                )
                await self._configure_providers(session, call)
                output = await answer_call(session, call)
            elif frame_type == "speech_start":
                output = await speech_started(session, call, int(frame.get("heard_response_boundary_ms", 0)))
            elif frame_type == "audio":
                result.outputs.extend(await self._handle_audio(session, call, frame))
            elif frame_type == "transcript":
                output = await handle_user_turn(
                    session,
                    call,
                    str(frame.get("text", "")),
                    start_ms=int(frame.get("start_ms", self.duration_ms)),
                    end_ms=int(frame.get("end_ms", self.duration_ms + 500)),
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
                output = await handle_dtmf(session, call, str(frame.get("digit", "")), self.duration_ms)
            elif frame_type == "silence":
                output = await handle_silence(session, call)
            elif frame_type == "voicemail":
                output = await handle_voicemail(session, call)
            elif frame_type == "hangup":
                await hangup_call(session, call)
                await self._store_recording(session, call)
                await session.commit()
                return FrameResult(ended=True)
            else:
                await append_event(session, call, "media.unknown_frame", {"type": frame_type})
            if output is not None:
                result.outputs.append(output)
            result.call_finished = call.ended_at is not None and call.status != "transferring"
            await session.commit()
            return result

    async def disconnect(self, reason: str = "media_disconnect") -> None:
        async with self.session_factory() as session:
            call = await session.get(VoiceCall, self.call_id)
            if call is not None:
                await hangup_call(session, call, reason)
                await self._store_recording(session, call)
                await session.commit()

    def close(self) -> None:
        if self._tts_token is not None:
            reset_voice_tts(self._tts_token)
            self._tts_token = None

    async def _configure_providers(self, session: AsyncSession, call: VoiceCall) -> None:
        try:
            tts = self.tts_factory(self.codec, self.sample_rate_hz)
        except Exception as exc:
            tts = LocalStreamingTTS()
            await append_event(
                session, call, "provider.fallback.activated", {"stage": "tts", "reason": type(exc).__name__}
            )
        self.close()
        self._tts_token = use_voice_tts(tts)
        await append_event(session, call, "provider.selected", {"stage": "tts", "provider": tts.name})
        if self.utterance_stt is None:
            return
        if decode_to_pcm16(b"", self.codec) is None:
            await append_event(session, call, "provider.unsupported_codec", {"stage": "stt", "codec": self.codec})
            return
        self.segmenter = UtteranceSegmenter(
            self.sample_rate_hz,
            threshold=self.settings.voice_vad_threshold,
            min_speech_ms=self.settings.voice_vad_min_speech_ms,
            end_silence_ms=self.settings.voice_vad_end_silence_ms,
            max_utterance_ms=self.settings.voice_vad_max_utterance_ms,
        )
        await append_event(session, call, "provider.selected", {"stage": "stt", "provider": self.utterance_stt.name})

    async def _handle_audio(self, session: AsyncSession, call: VoiceCall, frame: dict) -> list[VoiceOutput]:
        chunk = base64.b64decode(frame.get("audio", ""), validate=True)
        self.duration_ms += int(frame.get("duration_ms", 20))
        if len(self.audio_buffer) + len(chunk) <= self.settings.voice_max_recording_bytes:
            self.audio_buffer.extend(chunk)
        else:
            await append_event(
                session,
                call,
                "recording.truncated",
                {"limit_bytes": self.settings.voice_max_recording_bytes},
            )
        if self.segmenter is not None:
            return await self._handle_segmented_audio(session, call, chunk)
        output = await self._handle_hinted_audio(session, call, frame, chunk)
        return [output] if output is not None else []

    async def _handle_hinted_audio(
        self, session: AsyncSession, call: VoiceCall, frame: dict, chunk: bytes
    ) -> VoiceOutput | None:
        """Deterministic per-frame contract used by simulators and provider contract tests."""

        stt_started = time.perf_counter_ns()
        normalized = normalize_audio(chunk, self.codec, self.sample_rate_hz)
        provider = ReliableProvider(
            "stt",
            lambda: self.stt.transcribe(normalized, frame),
            lambda: self.fallback_stt.transcribe(b"", frame),
            timeout_seconds=self.settings.voice_stage_timeout_ms / 1000,
            breaker=self.stt_breaker,
            metrics=self.reliability,
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
        self._record_stt_latency(call, stt_started)
        if hypothesis and hypothesis.is_final:
            return await handle_user_turn(
                session,
                call,
                hypothesis.text,
                start_ms=int(frame.get("start_ms", max(0, self.duration_ms - 20))),
                end_ms=int(frame.get("end_ms", self.duration_ms)),
                confidence_bps=hypothesis.confidence_bps,
            )
        if hypothesis:
            await append_transcript(
                session,
                call,
                speaker="caller",
                text=hypothesis.text,
                start_ms=int(frame.get("start_ms", max(0, self.duration_ms - 20))),
                end_ms=int(frame.get("end_ms", self.duration_ms)),
                provider=self.stt.name,
                confidence_bps=hypothesis.confidence_bps,
                is_final=False,
            )
        return None

    async def _handle_segmented_audio(self, session: AsyncSession, call: VoiceCall, chunk: bytes) -> list[VoiceOutput]:
        pcm = decode_to_pcm16(chunk, self.codec) or b""
        outputs: list[VoiceOutput] = []
        for event in self.segmenter.push(pcm):
            if event.kind == "speech_start":
                # Barge-in: stop agent playback as soon as the caller talks over it.
                output = await speech_started(session, call)
            else:
                output = await self._transcribe_utterance(session, call, event)
            if output is not None:
                outputs.append(output)
        return outputs

    async def _transcribe_utterance(
        self, session: AsyncSession, call: VoiceCall, event: SegmentEvent
    ) -> VoiceOutput | None:
        stt_started = time.perf_counter_ns()
        provider = ReliableProvider(
            "stt",
            lambda: self.utterance_stt.transcribe_utterance(event.pcm, self.sample_rate_hz),
            _no_transcript,
            timeout_seconds=self.settings.voice_hosted_stt_timeout_ms / 1000,
            breaker=self.stt_breaker,
            metrics=self.reliability,
        )
        provider_result = await provider.run()
        self._record_stt_latency(call, stt_started)
        if provider_result.degraded:
            await append_event(
                session,
                call,
                "provider.fallback.activated",
                {"stage": "stt", "provider": self.utterance_stt.name},
            )
            # Treat an unrecognized turn like silence: re-prompt once, then a
            # second failure transfers the caller to a person.
            return await handle_silence(session, call)
        hypothesis = provider_result.value
        if hypothesis is None or not hypothesis.text.strip():
            await append_event(
                session, call, "speech.unrecognized", {"start_ms": event.start_ms, "end_ms": event.end_ms}
            )
            return None
        return await handle_user_turn(
            session,
            call,
            hypothesis.text,
            start_ms=event.start_ms,
            end_ms=event.end_ms,
            confidence_bps=hypothesis.confidence_bps,
            stt_provider=self.utterance_stt.name,
        )

    def _record_stt_latency(self, call: VoiceCall, started_ns: int) -> None:
        stt_ms = max(0, int((time.perf_counter_ns() - started_ns) / 1_000_000))
        metrics = {
            key: list(value) if isinstance(value, list) else value for key, value in call.latency_metrics.items()
        }
        metrics.setdefault("stt_ms", []).append(stt_ms)
        call.latency_metrics = metrics

    async def _store_recording(self, session: AsyncSession, call: VoiceCall) -> None:
        await store_recording(
            session,
            self.settings,
            call,
            bytes(self.audio_buffer),
            codec=self.codec,
            sample_rate_hz=self.sample_rate_hz,
            duration_ms=self.duration_ms,
        )
