import asyncio
import os

from redis.asyncio import Redis

from services.worker.omni_worker.jobs import JobEnvelope, execute_job

QUEUE_NAME = "omni:jobs"
DEAD_LETTER_QUEUE = "omni:jobs:dead"
MAX_ATTEMPTS = 3


async def process_message(redis: Redis, raw_message: bytes) -> None:
    job = JobEnvelope.model_validate_json(raw_message)
    dedupe_key = f"omni:job:completed:{job.idempotency_key}"
    if await redis.exists(dedupe_key):
        return
    try:
        await execute_job(job)
    except Exception:
        next_attempt = job.model_copy(update={"attempt": job.attempt + 1})
        target = QUEUE_NAME if next_attempt.attempt < MAX_ATTEMPTS else DEAD_LETTER_QUEUE
        await redis.rpush(target, next_attempt.model_dump_json())
        raise
    else:
        await redis.set(dedupe_key, "1", ex=7 * 24 * 60 * 60)


async def run_worker() -> None:
    # redis-py 8 defaults to a five-second socket timeout. Keep the socket
    # timeout longer than BLPOP so an empty queue is treated as normal idle
    # time instead of terminating the worker.
    redis = Redis.from_url(
        os.getenv("OMNI_REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=5,
        socket_timeout=10,
        health_check_interval=30,
    )
    try:
        while True:
            message = await redis.blpop(QUEUE_NAME, timeout=5)
            if message is not None:
                _, body = message
                try:
                    await process_message(redis, body)
                except Exception as exc:
                    print(f"Job failed: {exc}")
    finally:
        await redis.aclose()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
