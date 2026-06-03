"""Google ADK tool functions for the coordinator agent.

The coordinator orchestrates Claude Code workers: it holds the full issue list
for a job, fans out workers concurrently via asyncio, collects results, and
updates job state in Redis.

All tools are decorated with @tool so the ADK LlmAgent can call them during
its reasoning loop.  Async dispatch uses asyncio.gather so all issues are
worked in parallel — not sequentially.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google.adk.tools import tool  # type: ignore[import-untyped]

from issue_pilot.agents.github_tools import (
    create_branch,
    create_pull_request,
    fetch_issue,
)
from issue_pilot.pipeline.queue import get_sync_redis, sync_get_job, sync_update_job
from issue_pilot.schemas import IssuePlan, JobPayload, JobStatus
from issue_pilot.workers.claude_agent import dispatch_claude_worker

logger = logging.getLogger(__name__)


# ── internal state per coordinator run ───────────────────────────────────────
# Keyed by job_id → list of asyncio.Task (one per issue)
_worker_tasks: dict[str, list[asyncio.Task]] = {}


# ── ADK tools ────────────────────────────────────────────────────────────────

@tool
def load_job(job_id: str) -> dict[str, Any]:
    """Load job details from Redis and return repo_url, issue_numbers, create_pr, base_branch."""
    r = get_sync_redis()
    job = sync_get_job(r, job_id)
    if job is None:
        return {"error": f"Job {job_id} not found"}
    return {
        "job_id": job.job_id,
        "repo_url": job.repo_url,
        "issue_numbers": job.issue_numbers,
        "create_pr": job.create_pr,
        "base_branch": job.base_branch,
    }


@tool
def fetch_all_issues(repo_url: str, issue_numbers: list[int]) -> list[dict[str, Any]]:
    """Fetch GitHub issue metadata for every issue number in the list.
    Returns a list of issue dicts with title, body, labels, comments."""
    results = []
    for num in issue_numbers:
        try:
            results.append(fetch_issue(repo_url, num))
        except Exception as exc:
            logger.warning("Could not fetch issue #%d: %s", num, exc)
            results.append({
                "number": num, "title": f"Issue #{num}",
                "body": "", "labels": [], "state": "open", "comments": [],
            })
    return results


@tool
def prepare_branches(
    repo_url: str,
    issue_numbers: list[int],
    job_id: str,
    base_branch: str,
) -> dict[int, str]:
    """Create one git branch per issue; return mapping {issue_number: branch_name}."""
    mapping: dict[int, str] = {}
    for num in issue_numbers:
        branch = f"issue-pilot/{job_id[:8]}/issue-{num}"
        try:
            create_branch(repo_url, branch, base_branch)
            mapping[num] = branch
        except Exception as exc:
            logger.error("Failed to create branch for #%d: %s", num, exc)
            mapping[num] = branch  # worker will create it locally if needed
    return mapping


@tool
def dispatch_all_workers(
    job_id: str,
    repo_url: str,
    base_branch: str,
    issues: list[dict[str, Any]],
    branches: dict[str, str],  # ADK serialises int keys as str
) -> str:
    """Fan out one Claude Code worker per issue using asyncio.gather.

    Workers run concurrently — this call blocks until ALL workers complete
    (or timeout individually).  Results are stored back into Redis.
    Returns 'done' when all workers have finished.
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(
        _gather_workers(job_id, repo_url, base_branch, issues, branches)
    )


async def _gather_workers(
    job_id: str,
    repo_url: str,
    base_branch: str,
    issues: list[dict[str, Any]],
    branches: dict[str, str],
) -> str:
    coros = []
    for issue in issues:
        num = issue["number"]
        branch = branches.get(str(num), f"issue-pilot/{job_id[:8]}/issue-{num}")
        coros.append(
            dispatch_claude_worker(
                job_id=job_id,
                repo_url=repo_url,
                issue_number=num,
                base_branch=base_branch,
                issue_data=issue,
                branch_name=branch,
            )
        )

    logger.info("[coordinator] Dispatching %d Claude Code workers in parallel", len(coros))
    results = await asyncio.gather(*coros, return_exceptions=True)

    r = get_sync_redis()
    job = sync_get_job(r, job_id)
    if job is None:
        return "error: job not found"

    plans: list[IssuePlan] = []
    for res in results:
        if isinstance(res, Exception):
            logger.error("Worker raised exception: %s", res)
            continue
        if isinstance(res, dict):
            plans.append(
                IssuePlan(
                    issue_number=res.get("issue_number", 0),
                    title=res.get("title", ""),
                    plan=res.get("plan", ""),
                )
            )

    job.issue_plans = [p.model_dump() for p in plans]
    sync_update_job(r, job)
    logger.info("[coordinator] All workers done — %d plans collected", len(plans))
    return "done"


@tool
def create_pull_requests(
    job_id: str,
    repo_url: str,
    base_branch: str,
    branches: dict[str, str],
    issues: list[dict[str, Any]],
) -> str:
    """Open one PR per issue branch; store the first PR URL on the job. Returns PR URLs."""
    r = get_sync_redis()
    job = sync_get_job(r, job_id)
    plans_by_num = {p["issue_number"]: p for p in (job.issue_plans if job else [])}

    pr_urls: list[str] = []
    for issue in issues:
        num = issue["number"]
        branch = branches.get(str(num))
        if not branch:
            continue
        plan = plans_by_num.get(num, {})
        try:
            url = create_pull_request(
                repo_url=repo_url,
                branch_name=branch,
                base_branch=base_branch,
                title=f"[IssuePilot] Fix #{num}: {issue.get('title', '')}",
                body=plan.get("plan", "(no plan generated)"),
            )
            pr_urls.append(url)
        except Exception as exc:
            logger.error("Failed to create PR for #%d: %s", num, exc)

    if job and pr_urls:
        job.pr_url = pr_urls[0]
        job.status = JobStatus.done
        sync_update_job(r, job)

    return json.dumps(pr_urls)


@tool
def mark_job_done(job_id: str) -> str:
    """Mark the job status as done in Redis."""
    r = get_sync_redis()
    job = sync_get_job(r, job_id)
    if job:
        job.status = JobStatus.done
        sync_update_job(r, job)
    return f"Job {job_id} complete"
