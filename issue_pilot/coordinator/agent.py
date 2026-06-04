"""Google ADK LlmAgent — the IssuePilot coordinator.

Responsibilities
────────────────
1. Load the job from Redis (repo URL + issue list).
2. Fetch all GitHub issue metadata in one batch.
3. Pre-create one branch per issue on the remote repo.
4. Fan out ALL issues to Claude Code workers simultaneously (asyncio.gather).
5. Optionally open a PR per issue once workers complete.
6. Mark the job done.

The coordinator uses Gemini (fast model) for orchestration logic.
Heavy thinking / code understanding is delegated to the Claude Code workers.

Usage (called by pipeline/worker.py)
─────
    from issue_pilot.coordinator.agent import run_coordinator
    run_coordinator(job_id)
"""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent  # type: ignore[import-untyped]
from google.adk.runners import Runner  # type: ignore[import-untyped]
from google.adk.sessions import InMemorySessionService  # type: ignore[import-untyped]
from google.adk.types import Content, Part  # type: ignore[import-untyped]
from google.generativeai import configure as configure_genai  # type: ignore[import-untyped]

from issue_pilot.config import settings
from issue_pilot.coordinator.tools import (
    create_pull_requests,
    dispatch_all_workers,
    fetch_all_issues,
    load_job,
    mark_job_done,
    prepare_branches,
)

logger = logging.getLogger(__name__)

_COORDINATOR_SYSTEM = """\
You are IssuePilot Coordinator — an engineering manager AI that orchestrates
a fleet of Claude Code worker agents to fix GitHub issues in parallel.

You have these tools available:
  load_job             — load job details from Redis (repo_url, issue_numbers, …)
  fetch_all_issues     — fetch GitHub metadata for every issue at once
  prepare_branches     — create one git branch per issue on the remote repo
  dispatch_all_workers — launch ALL Claude Code workers simultaneously and wait
  create_pull_requests — open a PR per issue branch (only if create_pr=True)
  mark_job_done        — persist final job status to Redis

Strict workflow — follow this exact order, one tool per step:
  1. load_job(job_id)
  2. fetch_all_issues(repo_url, issue_numbers)
  3. prepare_branches(repo_url, issue_numbers, job_id, base_branch)
  4. dispatch_all_workers(job_id, repo_url, base_branch, issues, branches)
     — this blocks until all Claude Code workers finish; do NOT split it up.
  5. If create_pr is True: create_pull_requests(…)
  6. mark_job_done(job_id)

Rules:
- Do NOT process issues one-by-one; dispatch_all_workers handles all in parallel.
- Do NOT add extra commentary between steps — call the next tool immediately.
- If a step returns an error string, log it and continue to mark_job_done.
"""

_COORDINATOR_AGENT = LlmAgent(
    name="issue-pilot-coordinator",
    model=settings.COORDINATOR_MODEL,
    instruction=_COORDINATOR_SYSTEM,
    tools=[
        load_job,
        fetch_all_issues,
        prepare_branches,
        dispatch_all_workers,
        create_pull_requests,
        mark_job_done,
    ],
)


def run_coordinator(job_id: str) -> None:
    """Entry point called by the pipeline worker for each queued job."""
    configure_genai(api_key=settings.GOOGLE_API_KEY)

    session_service = InMemorySessionService()
    runner = Runner(
        agent=_COORDINATOR_AGENT,
        app_name="issue_pilot",
        session_service=session_service,
    )

    kickoff = Content(
        role="user",
        parts=[Part(text=f"Process job_id={job_id}. Follow your workflow exactly.")],
    )

    logger.info("[coordinator] Starting for job %s", job_id)
    for event in runner.run(
        user_id="pipeline",
        session_id=f"coord-{job_id}",
        new_message=kickoff,
    ):
        if event.is_final_response():
            logger.info("[coordinator] Finished job %s", job_id)
            break
        # Log intermediate tool activity
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    logger.debug("[coordinator] %s", part.text[:200])
