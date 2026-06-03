"""Postgres persistence layer for IssuePilot jobs and per-issue task status.

Two tables:
  issue_pilot_jobs        — one row per job (repo + issue list + stage)
  issue_pilot_issue_tasks — one row per (job, issue_number)

Usage pattern:
  • FastAPI routes  → async wrappers via asyncio.to_thread()
  • Coordinator tools (sync) → call db.* functions directly
  • pipeline/worker.py (sync) → call db.* functions directly

Schema is created automatically on first call to ensure_schema().
Call ensure_schema() from main.py lifespan.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from issue_pilot.config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


# ── connection pool ───────────────────────────────────────────────────────────

def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=settings.DATABASE_URL,
        )
    return _pool


@contextmanager
def _conn() -> Generator[psycopg2.extensions.connection, None, None]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── schema bootstrap ──────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS issue_pilot_jobs (
    job_id          TEXT PRIMARY KEY,
    repo_url        TEXT NOT NULL,
    issue_numbers   JSONB NOT NULL,
    create_pr       BOOLEAN NOT NULL DEFAULT TRUE,
    base_branch     TEXT NOT NULL DEFAULT 'main',
    stage           TEXT NOT NULL DEFAULT 'queued',
    workers_done    INTEGER NOT NULL DEFAULT 0,
    workers_total   INTEGER NOT NULL DEFAULT 0,
    pr_url          TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ipj_repo     ON issue_pilot_jobs (repo_url);
CREATE INDEX IF NOT EXISTS idx_ipj_stage    ON issue_pilot_jobs (stage);
CREATE INDEX IF NOT EXISTS idx_ipj_created  ON issue_pilot_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS issue_pilot_issue_tasks (
    id              SERIAL PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES issue_pilot_jobs(job_id) ON DELETE CASCADE,
    issue_number    INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    title           TEXT,
    plan            TEXT,
    branch_name     TEXT,
    pr_url          TEXT,
    files_changed   JSONB NOT NULL DEFAULT '[]',
    tests_passed    BOOLEAN,
    commit_sha      TEXT,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    UNIQUE (job_id, issue_number)
);

CREATE INDEX IF NOT EXISTS idx_ipit_job ON issue_pilot_issue_tasks (job_id);
"""


def ensure_schema() -> None:
    """Idempotent schema migration — safe to call on every startup."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
    logger.info("IssuePilot Postgres schema ready")


# ── job CRUD ──────────────────────────────────────────────────────────────────

def create_job(
    job_id: str,
    repo_url: str,
    issue_numbers: list[int],
    create_pr: bool,
    base_branch: str,
    created_at: datetime,
) -> None:
    now = datetime.now(tz=timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO issue_pilot_jobs
                    (job_id, repo_url, issue_numbers, create_pr, base_branch,
                     stage, workers_total, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (
                    job_id,
                    repo_url,
                    json.dumps(issue_numbers),
                    create_pr,
                    base_branch,
                    len(issue_numbers),
                    created_at,
                    now,
                ),
            )
            # Seed per-issue task rows
            for num in issue_numbers:
                cur.execute(
                    """
                    INSERT INTO issue_pilot_issue_tasks (job_id, issue_number, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT (job_id, issue_number) DO NOTHING
                    """,
                    (job_id, num),
                )


def update_job_stage(
    job_id: str,
    stage: str,
    *,
    workers_done: int | None = None,
    pr_url: str | None = None,
    error: str | None = None,
) -> None:
    parts = ["stage = %s", "updated_at = %s"]
    vals: list[Any] = [stage, datetime.now(tz=timezone.utc)]
    if workers_done is not None:
        parts.append("workers_done = %s")
        vals.append(workers_done)
    if pr_url is not None:
        parts.append("pr_url = %s")
        vals.append(pr_url)
    if error is not None:
        parts.append("error = %s")
        vals.append(error)
    vals.append(job_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE issue_pilot_jobs SET {', '.join(parts)} WHERE job_id = %s",
                vals,
            )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM issue_pilot_jobs WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_jobs(
    repo_url: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """Returns (total_count, rows)."""
    where = "WHERE repo_url = %s" if repo_url else ""
    params_count: tuple = (repo_url,) if repo_url else ()
    params_list: tuple = (repo_url, limit, offset) if repo_url else (limit, offset)

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM issue_pilot_jobs {where}",
                params_count,
            )
            total: int = cur.fetchone()["count"]  # type: ignore[index]

            cur.execute(
                f"""
                SELECT * FROM issue_pilot_jobs {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params_list,
            )
            rows = [dict(r) for r in cur.fetchall()]

    return total, rows


# ── issue-task CRUD ───────────────────────────────────────────────────────────

def update_issue_task(
    job_id: str,
    issue_number: int,
    status: str,
    *,
    title: str | None = None,
    plan: str | None = None,
    branch_name: str | None = None,
    pr_url: str | None = None,
    files_changed: list[str] | None = None,
    tests_passed: bool | None = None,
    commit_sha: str | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    parts = ["status = %s"]
    vals: list[Any] = [status]

    for col, val in [
        ("title", title),
        ("plan", plan),
        ("branch_name", branch_name),
        ("pr_url", pr_url),
        ("tests_passed", tests_passed),
        ("commit_sha", commit_sha),
        ("error", error),
        ("started_at", started_at),
        ("completed_at", completed_at),
    ]:
        if val is not None:
            parts.append(f"{col} = %s")
            vals.append(val)
    if files_changed is not None:
        parts.append("files_changed = %s")
        vals.append(json.dumps(files_changed))

    vals.extend([job_id, issue_number])
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE issue_pilot_issue_tasks
                SET {', '.join(parts)}
                WHERE job_id = %s AND issue_number = %s
                """,
                vals,
            )


def get_issue_tasks(job_id: str) -> list[dict[str, Any]]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM issue_pilot_issue_tasks
                WHERE job_id = %s
                ORDER BY issue_number
                """,
                (job_id,),
            )
            return [dict(r) for r in cur.fetchall()]
