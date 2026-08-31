"""Format findings as GitHub PR review comments (doc section 14)."""
import logging
from collections import defaultdict

from app.services.code_review.schemas import Finding, CommentLevel, FindingCategory

logger = logging.getLogger(__name__)


def format_finding_comment(finding: Finding) -> str:
    """Format a single finding as a detailed GitHub review comment.

    Matches doc section 14's "Better" example: specific, actionable, grounded.
    """
    lines = []
    lines.append(f"**{finding.category.value.title()} Issue**: {finding.severity.value.upper()}")

    if finding.evidence:
        lines.append("")
        lines.append("**Evidence**:")
        for evidence in finding.evidence[:3]:  # Limit to 3 top snippets
            lines.append(f"- `{evidence[:80]}`")

    if finding.suggested_fix:
        lines.append("")
        lines.append(f"**Suggestion**: {finding.suggested_fix}")

    if finding.agent_notes:
        lines.append("")
        lines.append(f"_Agent notes: {finding.agent_notes}_")

    return "\n".join(lines)


def format_summary_comment(
    findings: list[Finding],
    blocked_findings: list[Finding],
) -> str:
    """Format a summary comment with counts and status (doc section 14).

    Shows category breakdown, confidence/severity distribution, and overall status.
    """
    # Count by category and severity
    by_category = defaultdict(lambda: defaultdict(int))
    for finding in findings:
        by_category[finding.category.value][finding.severity.value] += 1

    blocked_by_category = defaultdict(int)
    for finding in blocked_findings:
        blocked_by_category[finding.category.value] += 1

    lines = []
    lines.append("## AI Code Review Summary")
    lines.append("")

    # Overall status
    if blocked_findings:
        lines.append("**Status**: ⚠️ Blocking Issues Found")
    elif findings:
        lines.append("**Status**: ℹ️ Issues Found")
    else:
        lines.append("**Status**: ✓ No issues found")

    lines.append("")
    lines.append(f"**Total findings**: {len(findings)}")

    # Per-category breakdown
    lines.append("")
    lines.append("**By category**:")
    for category in FindingCategory:
        if category.value in by_category:
            total = sum(by_category[category.value].values())
            blocked = blocked_by_category.get(category.value, 0)
            status = "⚠️" if blocked > 0 else "ℹ️"
            lines.append(f"- {status} **{category.value.title()}**: {total} finding(s)")
            if blocked > 0:
                lines.append(f"  - **{blocked} blocking**")

    lines.append("")
    lines.append(
        "_Review powered by AI (Claude). "
        "Mark findings as helpful, not helpful, or false positive to improve accuracy._"
    )

    return "\n".join(lines)
