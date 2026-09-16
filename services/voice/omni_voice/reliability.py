import asyncio
import time
from collections import Counter, deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int, reset_seconds: float):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.failures = 0
        self.opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        if not self.opened_at:
            return CircuitState.CLOSED
        if time.monotonic() - self.opened_at >= self.reset_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def allow(self) -> None:
        if self.state == CircuitState.OPEN:
            raise CircuitOpenError("provider circuit is open")

    def success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    value: T
    provider: str
    degraded: bool


class ReliableProvider(Generic[T]):
    """Apply a timeout, circuit breaker, and one bounded fallback attempt."""

    def __init__(
        self,
        name: str,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[], Awaitable[T]],
        *,
        timeout_seconds: float,
        breaker: CircuitBreaker,
        metrics: "ReliabilityMetrics",
    ):
        self.name = name
        self.primary = primary
        self.fallback = fallback
        self.timeout_seconds = timeout_seconds
        self.breaker = breaker
        self.metrics = metrics

    async def run(self) -> ProviderResult[T]:
        started = time.perf_counter()
        try:
            self.breaker.allow()
            value = await asyncio.wait_for(self.primary(), timeout=self.timeout_seconds)
            self.breaker.success()
            self.metrics.observe(f"{self.name}_latency_ms", (time.perf_counter() - started) * 1000)
            return ProviderResult(value=value, provider="primary", degraded=False)
        except (TimeoutError, CircuitOpenError, Exception) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.failure()
            self.metrics.increment(f"{self.name}_failures_total")
            if isinstance(exc, TimeoutError):
                self.metrics.increment(f"{self.name}_timeouts_total")
            self.metrics.increment(f"{self.name}_fallbacks_total")
            value = await asyncio.wait_for(self.fallback(), timeout=self.timeout_seconds)
            return ProviderResult(value=value, provider="fallback", degraded=True)


@dataclass(frozen=True)
class MediaFrame:
    payload: dict
    received_at_ms: int


class BoundedMediaBuffer:
    """A drop-oldest buffer that never allows live audio to grow without bound."""

    def __init__(self, capacity: int, stale_after_ms: int, metrics: "ReliabilityMetrics"):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.stale_after_ms = stale_after_ms
        self.metrics = metrics
        self._frames: deque[MediaFrame] = deque()

    def push(self, payload: dict, now_ms: int | None = None) -> bool:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        sent_at_ms = int(payload.get("sent_at_ms", now_ms))
        if now_ms - sent_at_ms > self.stale_after_ms:
            self.metrics.increment("media_frames_stale_total")
            return False
        if len(self._frames) == self.capacity:
            self._frames.popleft()
            self.metrics.increment("media_frames_overflow_total")
        self._frames.append(MediaFrame(payload=payload, received_at_ms=now_ms))
        self.metrics.set_gauge("media_queue_depth", len(self._frames))
        self.metrics.maximum("media_queue_high_water", len(self._frames))
        return True

    def pop(self) -> MediaFrame | None:
        frame = self._frames.popleft() if self._frames else None
        self.metrics.set_gauge("media_queue_depth", len(self._frames))
        return frame

    def __len__(self) -> int:
        return len(self._frames)


class AsyncBoundedMediaBuffer:
    """Async producer/consumer wrapper used between the socket and pipeline."""

    def __init__(self, capacity: int, stale_after_ms: int, metrics: "ReliabilityMetrics"):
        self.buffer = BoundedMediaBuffer(capacity, stale_after_ms, metrics)
        self._changed = asyncio.Condition()
        self._closed = False

    async def push(self, payload: dict, now_ms: int | None = None) -> bool:
        async with self._changed:
            accepted = self.buffer.push(payload, now_ms)
            if accepted:
                self._changed.notify()
            return accepted

    async def pop(self) -> MediaFrame | None:
        async with self._changed:
            await self._changed.wait_for(lambda: bool(self.buffer) or self._closed)
            return self.buffer.pop()

    async def close(self) -> None:
        async with self._changed:
            self._closed = True
            self._changed.notify_all()


class SessionAdmission:
    def __init__(self, capacity: int, metrics: "ReliabilityMetrics"):
        self.capacity = capacity
        self.metrics = metrics
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.capacity:
                self.metrics.increment("sessions_load_shed_total")
                return False
            self._active += 1
            self.metrics.set_gauge("active_sessions", self._active)
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            self.metrics.set_gauge("active_sessions", self._active)


class ReliabilityMetrics:
    def __init__(self):
        self.counters: Counter[str] = Counter()
        self.gauges: dict[str, float] = {}
        self.observations: dict[str, list[float]] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def maximum(self, name: str, value: float) -> None:
        self.gauges[name] = max(value, self.gauges.get(name, value))

    def observe(self, name: str, value: float) -> None:
        samples = self.observations.setdefault(name, [])
        samples.append(value)
        if len(samples) > 1000:
            del samples[:-1000]

    def snapshot(self) -> dict:
        observations = {}
        for name, values in self.observations.items():
            ordered = sorted(values)
            observations[name] = {
                "count": len(values),
                "average": round(sum(values) / len(values), 2),
                "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            }
        return {"counters": dict(self.counters), "gauges": self.gauges, "observations": observations}

    def prometheus(self) -> str:
        lines: list[str] = []
        for name, value in sorted({**self.counters, **self.gauges}.items()):
            lines.append(f"omni_voice_{name} {value}")
        for name, stats in sorted(self.snapshot()["observations"].items()):
            lines.append(f"omni_voice_{name}_count {stats['count']}")
            lines.append(f"omni_voice_{name}_p95 {stats['p95']}")
        return "\n".join(lines) + "\n"
