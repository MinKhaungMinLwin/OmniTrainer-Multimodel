import pytest

from services.worker.omni_worker.jobs import JobEnvelope
from services.worker.omni_worker.main import DEAD_LETTER_QUEUE, QUEUE_NAME, process_message


class FakeRedis:
    def __init__(self):
        self.completed: set[str] = set()
        self.pushed: list[tuple[str, str]] = []

    async def exists(self, key: str) -> bool:
        return key in self.completed

    async def set(self, key: str, _: str, ex: int) -> None:
        assert ex > 0
        self.completed.add(key)

    async def rpush(self, queue: str, message: str) -> None:
        self.pushed.append((queue, message))


def test_job_envelope_round_trip():
    job = JobEnvelope(
        id="job-1",
        type="health.check",
        tenant_id="tenant-1",
        correlation_id="trace-1",
        idempotency_key="tenant-1:health:1",
    )

    restored = JobEnvelope.model_validate_json(job.model_dump_json())

    assert restored == job
    assert restored.attempt == 0


@pytest.mark.asyncio
async def test_successful_job_is_marked_idempotently():
    redis = FakeRedis()
    job = JobEnvelope(
        id="job-1",
        type="health.check",
        tenant_id="tenant-1",
        correlation_id="trace-1",
        idempotency_key="health-1",
    )

    await process_message(redis, job.model_dump_json().encode())
    await process_message(redis, job.model_dump_json().encode())

    assert redis.completed == {"omni:job:completed:health-1"}
    assert redis.pushed == []


@pytest.mark.asyncio
async def test_failed_job_retries_then_moves_to_dead_letter(monkeypatch):
    async def fail(_):
        raise RuntimeError("failure")

    monkeypatch.setattr("services.worker.omni_worker.main.execute_job", fail)
    redis = FakeRedis()
    job = JobEnvelope(
        id="job-2",
        type="health.check",
        tenant_id="tenant-1",
        correlation_id="trace-2",
        idempotency_key="health-2",
        attempt=1,
    )

    with pytest.raises(RuntimeError):
        await process_message(redis, job.model_dump_json().encode())
    assert redis.pushed[0][0] == QUEUE_NAME

    final_attempt = job.model_copy(update={"attempt": 2})
    with pytest.raises(RuntimeError):
        await process_message(redis, final_attempt.model_dump_json().encode())
    assert redis.pushed[1][0] == DEAD_LETTER_QUEUE
