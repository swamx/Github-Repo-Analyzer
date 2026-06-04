"""Standalone pipeline worker process.

    python -m issue_pilot.pipeline.worker

Loop
────
1. Block-read one job_id from the Redis Stream (consumer group "workers").
2. Load the JobPayload from Redis and mark it as 'processing'.
3. Hand off to the Google ADK coordinator which:
      a. Fetches GitHub issue metadata for all issues in the job.
      b. Creates one branch per issue.
      c. Fans out Claude Code workers (lean-ctx 3.0) in parallel.
      d. Opens PRs (if create_pr=True).
      e. Writes results back to Redis.
4. ACK the stream message so it won't be redelivered.

Only one coordinator runs per job — the coordinator itself fans out parallel
Claude Code workers for individual issues.
"""

from __future__ import annotations

import logging
import signal

from issue_pilot.coordinator.agent import run_coordinator
from issue_pilot.pipeline.queue import (
    ack_job,
    ensure_consumer_group,
    get_sync_redis,
    read_next_job,
    sync_get_job,
    sync_update_job,
)
from issue_pilot.schemas import JobPayload, JobStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

_RUNNING = True


def _handle_signal(sig: int, _frame: object) -> None:
    global _RUNNING
    logger.info("Signal %d received — shutting down after current job", sig)
    _RUNNING = False


def run_worker() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    r = get_sync_redis()
    ensure_consumer_group(r)
    logger.info("IssuePilot pipeline worker started")

    while _RUNNING:
        result = read_next_job(r, block_ms=5000)
        if result is None:
            continue

        msg_id, job_id = result
        job = sync_get_job(r, job_id)

        if job is None:
            logger.warning("Job %s not found — skipping", job_id)
            ack_job(r, msg_id)
            continue

        job.status = JobStatus.processing
        sync_update_job(r, job)

        try:
            # The coordinator handles everything: issue fetching, branch creation,
            # worker dispatch, PR creation, and status update.
            run_coordinator(job_id)
        except Exception as exc:
            logger.exception("Coordinator failed for job %s: %s", job_id, exc)
            job = sync_get_job(r, job_id) or job
            job.status = JobStatus.failed
            job.error = str(exc)
            sync_update_job(r, job)
        finally:
            ack_job(r, msg_id)

    logger.info("Worker stopped")


if __name__ == "__main__":
    run_worker()
