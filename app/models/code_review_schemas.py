"""API request/response models for code review endpoints."""
from typing import Optional
from pydantic import BaseModel, Field

from app.services.code_review.schemas import ReviewResult, Finding


class ReviewTriggerRequest(BaseModel):
    repo: str = Field(..., description="owner/repo or full GitHub URL")
    pr_number: int = Field(..., description="Pull request number")
    dry_run: bool = Field(False, description="Don't post to GitHub if true")
    info_threshold: Optional[float] = Field(None, description="Custom info threshold (0.0–1.0)")
    block_threshold: Optional[float] = Field(None, description="Custom block threshold (0.0–1.0)")


class ReviewTriggerResponse(BaseModel):
    status: str = "completed"
    data: ReviewResult


class FeedbackRequest(BaseModel):
    finding_id: str = Field(..., description="Finding ID from review")
    reaction: str = Field(
        ...,
        description="helpful, not_helpful, false_positive, already_known, fixed"
    )


class WebhookPayload(BaseModel):
    action: str
    pull_request: dict
    repository: dict


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str
