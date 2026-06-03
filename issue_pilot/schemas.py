from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, HttpUrl


# ── status / stage enums ──────────────────────────────────────────────────────

class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class JobStage(str, Enum):
    """Fine-grained pipeline stage — updated as the coordinator progresses."""
    queued            = "queued"
    fetching_issues   = "fetching_issues"
    creating_branches = "creating_branches"
    workers_running   = "workers_running"
    creating_prs      = "creating_prs"
    done              = "done"
    failed            = "failed"

    @property
    def label(self) -> str:
        return {
            "queued":            "Waiting in queue",
            "fetching_issues":   "Fetching issue metadata from GitHub",
            "creating_branches": "Creating fix branches on GitHub",
            "workers_running":   "Claude Code agents researching & fixing issues",
            "creating_prs":      "Opening pull requests",
            "done":              "Complete",
            "failed":            "Failed",
        }[self.value]


class IssueTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    done    = "done"
    failed  = "failed"


# ── inbound ───────────────────────────────────────────────────────────────────

class FixRequest(BaseModel):
    repo_url: HttpUrl
    issue_numbers: list[int]
    create_pr: bool = True
    base_branch: str = "main"


# ── outbound — submit ─────────────────────────────────────────────────────────

class JobEnqueued(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    status_url: str
    stream_url: str    # SSE endpoint for live stage updates


# ── outbound — per-issue task ─────────────────────────────────────────────────

class IssuePlan(BaseModel):
    """Kept for backward-compat with coordinator result collection."""
    issue_number: int
    title: str
    plan: str


class IssueTaskDetail(BaseModel):
    issue_number: int
    status: IssueTaskStatus
    title: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    plan: str | None = None
    files_changed: list[str] = []
    tests_passed: bool | None = None
    commit_sha: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ── outbound — job status (polling + SSE) ────────────────────────────────────

class JobResult(BaseModel):
    job_id: str
    repo_url: str
    stage: JobStage
    stage_label: str
    workers_done: int = 0
    workers_total: int = 0
    issue_tasks: list[IssueTaskDetail] = []
    pr_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


# ── outbound — task list ──────────────────────────────────────────────────────

class TaskListItem(BaseModel):
    job_id: str
    repo_url: str
    issue_numbers: list[int]
    stage: JobStage
    stage_label: str
    workers_done: int
    workers_total: int
    pr_url: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    tasks: list[TaskListItem]


# ── internal pipeline payload (stored in Redis Hash for queue, Postgres for state) ──

class JobPayload(BaseModel):
    job_id: str
    repo_url: str
    issue_numbers: list[int]
    create_pr: bool
    base_branch: str
    status: JobStatus = JobStatus.queued
    issue_plans: list[dict[str, Any]] = []
    branch_name: str | None = None
    pr_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
