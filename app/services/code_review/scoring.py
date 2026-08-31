"""Confidence/severity scoring and policy enforcement (doc section 12–13)."""
import logging
from typing import Optional

from app.config import settings
from app.services.code_review.schemas import (
    Finding,
    CommentLevel,
    Severity,
)

logger = logging.getLogger(__name__)


def score_findings(
    findings: list[Finding],
    info_threshold: Optional[float] = None,
    block_threshold: Optional[float] = None,
) -> tuple[list[Finding], dict[str, int]]:
    """Apply policy thresholds to findings (doc section 12–13).

    Args:
        findings: Deduplicated findings
        info_threshold: Confidence threshold for informational comments (default: 0.65)
        block_threshold: Confidence threshold for blocking comments (default: 0.85)

    Returns:
        Tuple of:
        - scored_findings: findings with added _comment_level
        - summary: {CRITICAL: N, HIGH: N, MEDIUM: N, ...} counts
    """
    info_threshold = info_threshold or settings.REVIEW_CONFIDENCE_INFO_THRESHOLD
    block_threshold = block_threshold or settings.REVIEW_CONFIDENCE_BLOCK_THRESHOLD

    # Add comment level to each finding (as internal field)
    scored = []
    for finding in findings:
        # Decision tree from doc section 12
        if finding.confidence < info_threshold:
            comment_level = CommentLevel.SUPPRESSED
        elif finding.confidence < block_threshold:
            comment_level = CommentLevel.INFORMATIONAL
        else:
            # High confidence + high severity → blocking
            if finding.severity in (Severity.HIGH, Severity.CRITICAL):
                comment_level = CommentLevel.BLOCKING
            else:
                comment_level = CommentLevel.INFORMATIONAL

        # Store comment level as private field via model_copy
        scored_finding = finding.model_copy()
        scored_finding._comment_level = comment_level  # type: ignore
        scored.append(scored_finding)

    # Tally by severity
    summary = {}
    for severity in Severity:
        summary[severity.value] = sum(
            1 for f in scored if f.severity == severity
        )

    logger.info(
        "Scored %d findings: %s, policy thresholds info=%.2f block=%.2f",
        len(findings),
        summary,
        info_threshold,
        block_threshold,
    )
    return scored, summary


def filter_by_comment_level(findings: list[Finding], level: CommentLevel) -> list[Finding]:
    """Filter findings that should be posted at a given level."""
    filtered = []
    for finding in findings:
        finding_level = getattr(finding, "_comment_level", CommentLevel.INFORMATIONAL)
        if finding_level in (level, CommentLevel.BLOCKING):  # BLOCKING always posts
            filtered.append(finding)
    return filtered
