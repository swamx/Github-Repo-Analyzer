"""Code review API endpoints (doc section 2, 14–16)."""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Header

from app.config import settings
from app.models.code_review_schemas import (
    ReviewTriggerRequest,
    ReviewTriggerResponse,
    FeedbackRequest,
    ErrorResponse,
)
from app.services.code_review.service import CodeReviewService
from app.services.code_review.github_client import AsyncGitHubReviewClient
from app.services.code_review.feedback_store import FeedbackStore

logger = logging.getLogger(__name__)

router = APIRouter()
_review_service = CodeReviewService()
_feedback_store = FeedbackStore()


@router.post(
    "/review",
    response_model=ReviewTriggerResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Trigger a code review",
    description="Manually trigger a code review for a pull request.",
)
async def trigger_review(request: ReviewTriggerRequest) -> ReviewTriggerResponse:
    """Trigger an AI code review (doc section 2).

    This endpoint synchronously reviews a PR and returns findings.
    Use this for manual testing or integrations that need immediate results.

    Example:
    ```json
    {
      "repo": "owner/repo",
      "pr_number": 42,
      "dry_run": true
    }
    ```
    """
    logger.info(
        "POST /review repo=%s pr=%d dry_run=%s",
        request.repo,
        request.pr_number,
        request.dry_run,
    )

    try:
        result = await _review_service.review_pull_request(
            repo=request.repo,
            pr_number=request.pr_number,
            dry_run=request.dry_run,
            info_threshold=request.info_threshold,
            block_threshold=request.block_threshold,
        )
        logger.info("Review completed: %d findings", len(result.findings))
        return ReviewTriggerResponse(status="completed", data=result)

    except ValueError as e:
        logger.warning("Bad request: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Review failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error during review")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@router.post(
    "/webhook",
    status_code=202,
    summary="GitHub webhook for PR reviews",
    description=(
        "Receive GitHub webhook events for pull_request actions "
        "(opened, synchronize, reopened) and trigger reviews asynchronously."
    ),
)
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
) -> dict:
    """GitHub webhook handler (doc section 2, 21).

    GitHub sends a POST to this endpoint when a PR is opened/updated.
    We verify the HMAC-SHA256 signature, filter for PR events, and queue
    the review asynchronously via BackgroundTasks.

    Requires GITHUB_WEBHOOK_SECRET to be set.
    """
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("Webhook received but GITHUB_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=501, detail="Webhooks not configured")

    # Verify signature (doc section 21)
    body = await request.body()
    if not x_hub_signature_256:
        logger.warning("Webhook request missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=401, detail="Missing signature header")

    if not AsyncGitHubReviewClient.verify_webhook_signature(
        body,
        x_hub_signature_256,
        settings.GITHUB_WEBHOOK_SECRET,
    ):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Signature verification failed")

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Webhook request body is not valid JSON")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Filter for pull_request events
    if payload.get("action") not in ("opened", "synchronize", "reopened"):
        logger.debug("Webhook action=%s, skipping", payload.get("action"))
        return {"status": "ignored", "reason": f"action={payload.get('action')}"}

    try:
        pr_data = payload.get("pull_request", {})
        repo_data = payload.get("repository", {})
        repo_name = repo_data.get("full_name", "unknown")
        pr_number = pr_data.get("number", 0)

        if not repo_name or not pr_number:
            logger.warning("Webhook payload missing repo/PR info")
            raise HTTPException(status_code=400, detail="Missing repo or PR number")

        logger.info("Webhook: queuing review for %s PR#%d", repo_name, pr_number)

        # Queue review in background
        background_tasks.add_task(
            _review_service.review_pull_request,
            repo=repo_name,
            pr_number=pr_number,
            dry_run=False,
        )

        return {"status": "queued", "repo": repo_name, "pr": pr_number}

    except Exception as e:
        logger.exception("Webhook processing failed")
        raise HTTPException(status_code=500, detail=f"Processing failed: {e}")


@router.post(
    "/feedback",
    summary="Record developer feedback",
    description="Record feedback on a finding (helpful, false_positive, etc).",
)
async def record_feedback(request: FeedbackRequest) -> dict:
    """Record developer feedback on findings (doc section 15).

    Developers can mark findings as helpful, false positives, fixed, etc.
    This data feeds the evaluation pipeline (doc section 16).
    """
    logger.info("Recording feedback: %s → %s", request.finding_id, request.reaction)

    try:
        if request.reaction not in ("helpful", "not_helpful", "false_positive", "already_known", "fixed"):
            raise ValueError(f"Invalid reaction: {request.reaction}")

        success = _feedback_store.record_feedback(request.finding_id, request.reaction)
        if not success:
            raise HTTPException(status_code=404, detail="Finding not found")

        return {"status": "recorded", "finding_id": request.finding_id, "reaction": request.reaction}

    except ValueError as e:
        logger.warning("Invalid feedback request: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to record feedback")
        raise HTTPException(status_code=500, detail=f"Failed to record: {e}")


@router.get(
    "/stats",
    summary="Get review statistics",
    description="Get aggregated metrics on reviews and feedback (doc section 16).",
)
async def get_stats(repository: str = None) -> dict:
    """Get review statistics (doc section 16 — online evaluation)."""
    try:
        stats = _feedback_store.get_stats(repository=repository)
        return {"status": "ok", "data": stats}
    except Exception as e:
        logger.exception("Failed to get stats")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {e}")
