import asyncio

import pytest

from evals.platform.release_gate import evaluate_release
from services.voice.omni_voice.reliability import (
    AsyncBoundedMediaBuffer,
    BoundedMediaBuffer,
    CircuitBreaker,
    CircuitState,
    ReliabilityMetrics,
    ReliableProvider,
    SessionAdmission,
)


def passing_candidate() -> dict:
    return {
        "version": "candidate",
        "samples": 20,
        "segments": {"noisy": 10, "interruptions": 10},
        "metrics": {
            "task_success_rate": 0.95,
            "grounded_response_rate": 0.98,
            "interruption_success_rate": 0.95,
            "safe_transfer_rate": 1.0,
            "false_action_rate": 0.0,
            "p95_first_audio_ms": 700,
            "cost_per_success_usd": 0.05,
        },
    }


def thresholds() -> dict:
    return {
        "minimum_samples": 8,
        "minimum": {"task_success_rate": 0.9, "safe_transfer_rate": 1.0},
        "maximum": {"false_action_rate": 0.01, "p95_first_audio_ms": 800},
        "maximum_regression": {"task_success_rate": 0.02},
    }


def test_media_buffer_is_bounded_and_drops_stale_frames():
    metrics = ReliabilityMetrics()
    buffer = BoundedMediaBuffer(2, 1000, metrics)
    assert buffer.push({"sequence": 1}, now_ms=2000)
    assert buffer.push({"sequence": 2}, now_ms=2000)
    assert buffer.push({"sequence": 3}, now_ms=2000)
    assert not buffer.push({"sequence": 4, "sent_at_ms": 1}, now_ms=2000)
    assert len(buffer) == 2
    assert buffer.pop().payload["sequence"] == 2
    assert metrics.counters["media_frames_overflow_total"] == 1
    assert metrics.counters["media_frames_stale_total"] == 1


@pytest.mark.asyncio
async def test_async_buffer_applies_real_producer_backpressure_and_admission_is_atomic():
    metrics = ReliabilityMetrics()
    buffer = AsyncBoundedMediaBuffer(2, 1000, metrics)
    await buffer.push({"sequence": 1}, now_ms=100)
    await buffer.push({"sequence": 2}, now_ms=100)
    await buffer.push({"sequence": 3}, now_ms=100)
    assert (await buffer.pop()).payload["sequence"] == 2

    admission = SessionAdmission(2, metrics)
    assert await asyncio.gather(admission.acquire(), admission.acquire()) == [True, True]
    assert not await admission.acquire()
    await admission.release()
    assert await admission.acquire()


@pytest.mark.asyncio
async def test_provider_timeout_uses_fallback_and_opens_circuit():
    metrics = ReliabilityMetrics()
    breaker = CircuitBreaker(1, 60)

    async def slow():
        await asyncio.sleep(0.02)
        return "late"

    async def fallback():
        return "safe fallback"

    provider = ReliableProvider("stt", slow, fallback, timeout_seconds=0.001, breaker=breaker, metrics=metrics)
    result = await provider.run()
    assert result.value == "safe fallback"
    assert result.degraded
    assert breaker.state == CircuitState.OPEN
    assert metrics.counters["stt_timeouts_total"] == 1
    second = await provider.run()
    assert second.provider == "fallback"


def test_release_gate_passes_complete_scorecard_and_rejects_regression():
    candidate = passing_candidate()
    assert evaluate_release(candidate, candidate, thresholds()).passed
    regressed = {**candidate, "metrics": {**candidate["metrics"], "task_success_rate": 0.8}}
    result = evaluate_release(candidate, regressed, thresholds())
    assert not result.passed
    assert any("task_success_rate" in failure for failure in result.failures)
