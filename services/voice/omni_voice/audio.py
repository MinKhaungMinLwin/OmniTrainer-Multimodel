"""Pure-Python telephony audio helpers.

`audioop` is deprecated and removed in Python 3.13, so G.711 mu-law coding,
resampling, and energy-based voice activity detection are implemented here.
Telephony frames are small (160 samples per 20 ms at 8 kHz), so per-sample
Python loops stay well inside the realtime budget.
"""

import io
import math
import wave
from array import array
from dataclasses import dataclass
from typing import Literal

PCM_CODECS = {"pcm16", "pcm16le", "l16"}
MULAW_CODECS = {"mulaw", "pcmu", "audio/x-mulaw"}

_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635


def _decode_mulaw_byte(value: int) -> int:
    value = ~value & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = (((mantissa << 3) + _MULAW_BIAS) << exponent) - _MULAW_BIAS
    return -sample if sign else sample


_MULAW_DECODE_TABLE = [_decode_mulaw_byte(value) for value in range(256)]


_MULAW_SEGMENT_ENDS = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)


def _encode_mulaw_sample(sample: int) -> int:
    # Sun G.711 reference (the algorithm audioop used): quantize to 14 bits
    # first, so negative samples round exactly as in standard encoders.
    value = sample >> 2
    if value < 0:
        value, mask = -value, 0x7F
    else:
        mask = 0xFF
    value = min(value, _MULAW_CLIP >> 2) + (_MULAW_BIAS >> 2)
    segment = next((index for index, end in enumerate(_MULAW_SEGMENT_ENDS) if value <= end), 8)
    if segment >= 8:
        return 0x7F ^ mask
    return ((segment << 4) | ((value >> (segment + 1)) & 0x0F)) ^ mask


def mulaw_to_pcm16(audio: bytes) -> bytes:
    return array("h", (_MULAW_DECODE_TABLE[value] for value in audio)).tobytes()


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    return bytes(_encode_mulaw_sample(sample) for sample in samples)


def decode_to_pcm16(audio: bytes, codec: str) -> bytes | None:
    """Return signed little-endian PCM16, or None for codecs this gateway cannot decode."""

    normalized = codec.lower()
    if normalized in MULAW_CODECS:
        return mulaw_to_pcm16(audio)
    if normalized in PCM_CODECS:
        return audio[: len(audio) - len(audio) % 2]
    return None


def encode_from_pcm16(pcm: bytes, codec: str) -> bytes:
    normalized = codec.lower()
    if normalized in MULAW_CODECS:
        return pcm16_to_mulaw(pcm)
    if normalized in PCM_CODECS:
        return pcm
    raise ValueError(f"Unsupported output codec: {codec}")


def resample_pcm16(pcm: bytes, source_rate_hz: int, target_rate_hz: int) -> bytes:
    """Resample mono PCM16 with averaging on decimation and linear interpolation otherwise."""

    if source_rate_hz == target_rate_hz or not pcm:
        return pcm
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return b""
    if source_rate_hz % target_rate_hz == 0:
        # Integer decimation (24 kHz TTS to 8 kHz telephony): a boxcar average
        # is a cheap low-pass filter that removes most aliasing.
        factor = source_rate_hz // target_rate_hz
        return array(
            "h",
            (
                int(sum(samples[index : index + factor]) / len(samples[index : index + factor]))
                for index in range(0, len(samples), factor)
            ),
        ).tobytes()
    target_count = max(1, round(len(samples) * target_rate_hz / source_rate_hz))
    last = len(samples) - 1
    output = array("h")
    for index in range(target_count):
        position = index * source_rate_hz / target_rate_hz
        left = min(last, int(position))
        right = min(last, left + 1)
        fraction = position - left
        output.append(int(samples[left] + (samples[right] - samples[left]) * fraction))
    return output.tobytes()


def wav_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Only 16-bit WAV audio is supported")
        frames = source.readframes(source.getnframes())
        channels = source.getnchannels()
        rate = source.getframerate()
    if channels == 1:
        return frames, rate
    samples = array("h", frames)
    mono = array("h", (samples[index] for index in range(0, len(samples), channels)))
    return mono.tobytes(), rate


def pcm16_to_wav(pcm: bytes, sample_rate_hz: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate_hz)
        target.writeframes(pcm)
    return output.getvalue()


def chunk_audio(audio: bytes, bytes_per_chunk: int) -> list[bytes]:
    return [audio[index : index + bytes_per_chunk] for index in range(0, len(audio), bytes_per_chunk)]


def rms(pcm: bytes) -> float:
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


@dataclass(frozen=True)
class SegmentEvent:
    kind: Literal["speech_start", "utterance"]
    start_ms: int
    end_ms: int
    pcm: bytes = b""


class UtteranceSegmenter:
    """Energy-based endpointing that turns a PCM16 stream into caller utterances.

    `speech_start` fires once speech has lasted `min_speech_ms`, which lets the
    runtime interrupt agent playback (barge-in). `utterance` fires after
    `end_silence_ms` of silence, or when an utterance reaches `max_utterance_ms`.
    """

    def __init__(
        self,
        sample_rate_hz: int,
        *,
        threshold: float,
        min_speech_ms: int,
        end_silence_ms: int,
        max_utterance_ms: int,
        pre_roll_ms: int = 200,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.end_silence_ms = end_silence_ms
        self.max_utterance_ms = max_utterance_ms
        self.pre_roll_bytes = sample_rate_hz * 2 * pre_roll_ms // 1000
        self._elapsed_ms = 0
        self._pre_roll = bytearray()
        self._utterance = bytearray()
        self._in_speech = False
        self._announced = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._start_ms = 0

    def push(self, pcm: bytes) -> list[SegmentEvent]:
        frame_ms = max(1, len(pcm) * 1000 // (self.sample_rate_hz * 2))
        frame_start = self._elapsed_ms
        self._elapsed_ms += frame_ms
        voiced = rms(pcm) >= self.threshold
        events: list[SegmentEvent] = []
        if not self._in_speech:
            if voiced:
                self._in_speech = True
                self._announced = False
                self._speech_ms = frame_ms
                self._silence_ms = 0
                self._start_ms = max(0, frame_start - len(self._pre_roll) * 1000 // (self.sample_rate_hz * 2))
                self._utterance = bytearray(self._pre_roll)
                self._utterance.extend(pcm)
                self._pre_roll.clear()
            else:
                self._pre_roll.extend(pcm)
                del self._pre_roll[: max(0, len(self._pre_roll) - self.pre_roll_bytes)]
                return events
        else:
            self._utterance.extend(pcm)
            if voiced:
                self._speech_ms += frame_ms
                self._silence_ms = 0
            else:
                self._silence_ms += frame_ms
        if not self._announced and self._speech_ms >= self.min_speech_ms:
            self._announced = True
            events.append(SegmentEvent("speech_start", self._start_ms, self._elapsed_ms))
        utterance_ms = self._elapsed_ms - self._start_ms
        if self._silence_ms >= self.end_silence_ms or utterance_ms >= self.max_utterance_ms:
            if self._announced:
                events.append(SegmentEvent("utterance", self._start_ms, self._elapsed_ms, bytes(self._utterance)))
            # A burst shorter than min_speech_ms is treated as noise and dropped.
            self._reset()
        return events

    def flush(self) -> SegmentEvent | None:
        event = None
        if self._in_speech and self._announced:
            event = SegmentEvent("utterance", self._start_ms, self._elapsed_ms, bytes(self._utterance))
        self._reset()
        return event

    def _reset(self) -> None:
        self._in_speech = False
        self._announced = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._utterance = bytearray()
