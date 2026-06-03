"""Redis Streams–based job queue for issue_pilot.

Producer  → XADD  issue_pilot:jobs  *  job_id <id>
Consumer  → XREADGROUP  GROUP workers  consumer-1  COUNT 1  BLOCK 0
                        STREAMS issue_pilot:jobs  >
ACK       → XACK  issue_pilot:jobs  workers  <message-id>

Job state is kept in a Redis Hash at key  issue_pilot:job:<job_id>
so that the API can poll status without touching the stream.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
import redis as syncredis

from issue_pilot.config import settings
from issue_pilot.schemas import JobPayload, JobStatus

logger = logging.getLogger(__name__)

# ── helpers ───────────────────────────────────────────────────────────────────

def _job_key(job_id: str) -> str:
    return f"issue_pilot:job:{job_id}"


# ── async API (used by FastAPI routes) ────────────────────────────────────────

async def get_async_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def enqueue_job(payload: JobPayload) -> None:
    """Store job state + push job_id onto the Redis Stream."""
    r = await get_async_redis()
    try:
        # Persist full payload in a Hash so /status can read it cheaply
        await r.set(
            _job_key(payload.job_id),
            payload.model_dump_json(),
            ex=settings.JOB_TTL_SECONDS,
        )
        # Only the job_id travels on the stream — worker fetches payload from Hash
        await r.xadd(settings.REDIS_STREAM, {"job_id": payload.job_id})
        logger.info("Enqueued job %s (%d issues)", payload.job_id, len(payload.issue_numbers))
    finally:
        await r.aclose()


async def get_job(job_id: str) -> JobPayload | None:
    r = await get_async_redis()
    try:
        raw = await r.get(_job_key(job_id))
        if raw is None:
            return None
        return JobPayload.model_validate_json(raw)
    finally:
        await r.aclose()


async def update_job(payload: JobPayload) -> None:
    payload.updated_at = datetime.now(tz=timezone.utc)
    r = await get_async_redis()
    try:
        await r.set(
            _job_key(payload.job_id),
            payload.model_dump_json(),
            ex=settings.JOB_TTL_SECONDS,
        )
    finally:
        await r.aclose()


# ── sync API (used by worker process) ────────────────────────────────────────

def get_sync_redis() -> syncredis.Redis:
    return syncredis.from_url(settings.REDIS_URL, decode_responses=True)


def ensure_consumer_group(r: syncredis.Redis) -> None:
    try:
        r.xgroup_create(settings.REDIS_STREAM, settings.REDIS_GROUP, id="0", mkstream=True)
        logger.info("Consumer group '%s' created on stream '%s'", settings.REDIS_GROUP, settings.REDIS_STREAM)
    except syncredis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def read_next_job(r: syncredis.Redis, block_ms: int = 5000) -> tuple[str, str] | None:
    """Blocking read; returns (stream_msg_id, job_id) or None on timeout."""
    messages = r.xreadgroup(
        groupname=settings.REDIS_GROUP,
        consumername=settings.REDIS_CONSUMER,
        streams={settings.REDIS_STREAM: ">"},
        count=1,
        block=block_ms,
    )
    if not messages:
        return None
    _stream, entries = messages[0]
    msg_id, fields = entries[0]
    return msg_id, fields["job_id"]


def ack_job(r: syncredis.Redis, msg_id: str) -> None:
    r.xack(settings.REDIS_STREAM, settings.REDIS_GROUP, msg_id)


def sync_get_job(r: syncredis.Redis, job_id: str) -> JobPayload | None:
    raw = r.get(_job_key(job_id))
    if raw is None:
        return None
    return JobPayload.model_validate_json(raw)


def sync_update_job(r: syncredis.Redis, payload: JobPayload) -> None:
    payload.updated_at = datetime.now(tz=timezone.utc)
    r.set(_job_key(payload.job_id), payload.model_dump_json(), ex=settings.JOB_TTL_SECONDS)
