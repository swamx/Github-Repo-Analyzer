"""Validation layer — treat LLM output as untrusted (doc section 10)."""
import logging
from typing import Optional

from app.services.code_review.schemas import Finding

logger = logging.getLogger(__name__)


def validate_findings(
    findings: list[Finding],
    changed_files: dict[str, str],
    changed_line_map: dict[str, set[int]],
) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Validate findings against the actual diff (doc section 10).

    Args:
        findings: LLM-produced findings (untrusted)
        changed_files: {file_path: patch}
        changed_line_map: {file_path: set of changed line numbers}

    Returns:
        Tuple of:
        - validated_findings: findings that passed all checks
        - dropped_findings: [(finding, reason), ...]
    """
    validated = []
    dropped = []

    for finding in findings:
        reasons = []

        # 1. Check file exists in diff
        if finding.file not in changed_files:
            reasons.append(f"File {finding.file} not in diff")

        # 2. Check line is in changed range
        elif finding.line not in changed_line_map.get(finding.file, set()):
            reasons.append(
                f"Line {finding.line} not in changed range for {finding.file}"
            )

        # 3. Check evidence substring exists in patch
        if finding.evidence and finding.file in changed_files:
            patch = changed_files[finding.file]
            evidence_found = False
            for evidence_snippet in finding.evidence:
                if evidence_snippet in patch:
                    evidence_found = True
                    break
            if not evidence_found:
                reasons.append(
                    f"Evidence not found in patch: '{finding.evidence[0][:50]}...'"
                )

        if reasons:
            dropped.append((finding, "; ".join(reasons)))
            logger.warning(
                "Dropped finding in %s:%d — %s",
                finding.file,
                finding.line,
                "; ".join(reasons),
            )
        else:
            validated.append(finding)

    return validated, dropped
