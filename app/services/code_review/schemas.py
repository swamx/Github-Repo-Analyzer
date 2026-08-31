"""Pydantic models for the code review pipeline (doc sections 3, 9, 12)."""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingCategory(str, Enum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    TESTING = "testing"


class CommentLevel(str, Enum):
    SUPPRESSED = "suppressed"
    INFORMATIONAL = "informational"
    BLOCKING = "blocking"


class ChangedFile(BaseModel):
    path: str
    status: str  # added, modified, deleted, renamed
    additions: int
    deletions: int
    patch: str  # unified diff


class RiskProfile(BaseModel):
    file: str
    level: RiskLevel
    reasons: list[str]


class Finding(BaseModel):
    file: str = Field(..., description="File path from the diff")
    line: int = Field(..., description="Changed line number where issue occurs")
    category: FindingCategory
    severity: Severity
    confidence: float = Field(..., ge=0.0, le=1.0, description="0.0–1.0")
    evidence: list[str] = Field(default_factory=list, description="Quotes/snippets supporting the finding")
    suggested_fix: str = Field(default="", description="Actionable fix suggestion")
    agent_notes: str = Field(default="", description="Agent reasoning about this finding")


class ReviewResult(BaseModel):
    repository: str
    pull_request: int
    head_commit: str
    status: str = "completed"
    findings: list[Finding]
    risk_summary: dict[str, int] = Field(default_factory=dict, description="Counts by category")
    comment_level_summary: dict[str, int] = Field(default_factory=dict, description="Counts by comment level")


class ReviewRequest(BaseModel):
    repo: str = Field(..., description="owner/repo or full GitHub URL")
    pr_number: int = Field(..., description="Pull request number")
    dry_run: bool = Field(
        False,
        description="If true, don't post to GitHub; just return findings."
    )


class FeedbackRequest(BaseModel):
    repository: str
    pull_request: int
    finding_id: str
    reaction: str = Field(..., description="helpful, not_helpful, false_positive, already_known, fixed")
