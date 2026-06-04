"""FastAPI router for the IssuePilot pipeline.

Mount in main.py:
    from issue_pilot.routes import router as issue_pilot_router
    app.include_router(issue_pilot_router, prefix="/api/issue-pilot", tags=["IssuePilot"])

Endpoints
─────────
POST /api/issue-pilot/fix
    Body : FixRequest  { repo_url, issue_numbers, create_pr, base_branch }
    Returns : JobEnqueued  { job_id, status, status_url, stream_url }

GET  /api/issue-pilot/status/{job_id}
    Returns : JobResult  — poll until stage=done|failed

GET  /api/issue-pilot/status/{job_id}/stream
    Returns : text/event-stream  — SSE updates every 3 s until terminal stage

GET  /api/issue-pilot/tasks
    Query   : repo_url (optional), limit (default 50), offset (default 0)
    Returns : TaskListResponse  — paginated list of all jobs for a repo
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from issue_pilot import db
from issue_pilot.pipeline.queue import enqueue_job
from issue_pilot.schemas import (
    FixRequest,
    IssueTaskDetail,
    IssueTaskStatus,
    JobEnqueued,
    JobPayload,
    JobResult,
    JobStage,
    JobStatus,
    TaskListItem,
    TaskListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_TERMINAL_STAGES = {JobStage.done, JobStage.failed}


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_job_result(job_row: dict, task_rows: list[dict]) -> JobResult:
    stage = JobStage(job_row["stage"])
    tasks = [
        IssueTaskDetail(
            issue_number=t["issue_number"],
            status=IssueTaskStatus(t["status"]),
            title=t.get("title"),
            branch_name=t.get("branch_name"),
            pr_url=t.get("pr_url"),
            plan=t.get("plan"),
            files_changed=t.get("files_changed") or [],
            tests_passed=t.get("tests_passed"),
            commit_sha=t.get("commit_sha"),
            error=t.get("error"),
            started_at=t.get("started_at"),
            completed_at=t.get("completed_at"),
        )
        for t in task_rows
    ]
    return JobResult(
        job_id=job_row["job_id"],
        repo_url=job_row["repo_url"],
        stage=stage,
        stage_label=stage.label,
        workers_done=job_row.get("workers_done", 0),
        workers_total=job_row.get("workers_total", 0),
        issue_tasks=tasks,
        pr_url=job_row.get("pr_url"),
        error=job_row.get("error"),
        created_at=job_row["created_at"],
        updated_at=job_row["updated_at"],
    )


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("/fix", response_model=JobEnqueued, status_code=202)
async def submit_fix_job(request: FixRequest) -> JobEnqueued:
    """Accept a repo URL + issue list, enqueue an async fix job, return job_id."""
    job_id = uuid.uuid4().hex
    now = datetime.now(tz=timezone.utc)

    payload = JobPayload(
        job_id=job_id,
        repo_url=str(request.repo_url),
        issue_numbers=request.issue_numbers,
        create_pr=request.create_pr,
        base_branch=request.base_branch,
        status=JobStatus.queued,
        created_at=now,
        updated_at=now,
    )

    # Write to Postgres first (source of truth for status queries)
    await asyncio.to_thread(
        db.create_job,
        job_id=job_id,
        repo_url=str(request.repo_url),
        issue_numbers=request.issue_numbers,
        create_pr=request.create_pr,
        base_branch=request.base_branch,
        created_at=now,
    )

    # Enqueue onto Redis Stream so the pipeline worker picks it up
    await enqueue_job(payload)

    logger.info(
        "Job %s queued — repo=%s issues=%s",
        job_id, request.repo_url, request.issue_numbers,
    )
    return JobEnqueued(
        job_id=job_id,
        status=JobStatus.queued,
        status_url=f"/api/issue-pilot/status/{job_id}",
        stream_url=f"/api/issue-pilot/status/{job_id}/stream",
    )


@router.get("/status/{job_id}", response_model=JobResult)
async def get_job_status(job_id: str) -> JobResult:
    """Poll the status and current pipeline stage of a fix job."""
    job_row = await asyncio.to_thread(db.get_job, job_id)
    if job_row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    task_rows = await asyncio.to_thread(db.get_issue_tasks, job_id)
    return _build_job_result(job_row, task_rows)


@router.get("/status/{job_id}/stream")
async def stream_job_status(job_id: str) -> StreamingResponse:
    """Server-Sent Events stream — pushes a JobResult JSON event every 3 s.

    Closes automatically when the job reaches stage 'done' or 'failed'.
    Connect with:
        const es = new EventSource('/api/issue-pilot/status/<id>/stream');
        es.onmessage = e => console.log(JSON.parse(e.data));
    """
    # Verify job exists before opening the stream
    job_row = await asyncio.to_thread(db.get_job, job_id)
    if job_row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    async def _generate():
        while True:
            job = await asyncio.to_thread(db.get_job, job_id)
            tasks = await asyncio.to_thread(db.get_issue_tasks, job_id)
            if job is None:
                yield "event: error\ndata: job not found\n\n"
                return
            result = _build_job_result(job, tasks)
            payload = result.model_dump_json()
            yield f"data: {payload}\n\n"
            if result.stage in _TERMINAL_STAGES:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(3)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # disable nginx buffering
        },
    )


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    repo_url: str | None = Query(None, description="Filter by GitHub repository URL"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TaskListResponse:
    """List all IssuePilot jobs, optionally filtered by repo URL."""
    total, rows = await asyncio.to_thread(
        db.list_jobs,
        repo_url=repo_url,
        limit=limit,
        offset=offset,
    )
    items = []
    for row in rows:
        stage = JobStage(row["stage"])
        items.append(
            TaskListItem(
                job_id=row["job_id"],
                repo_url=row["repo_url"],
                issue_numbers=row["issue_numbers"],
                stage=stage,
                stage_label=stage.label,
                workers_done=row.get("workers_done", 0),
                workers_total=row.get("workers_total", 0),
                pr_url=row.get("pr_url"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return TaskListResponse(total=total, limit=limit, offset=offset, tasks=items)
