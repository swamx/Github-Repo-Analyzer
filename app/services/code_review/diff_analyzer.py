"""Deterministic PR diff analysis (doc section 3 — no LLM)."""
import logging
import re
from typing import Optional

from app.services.code_review.schemas import RiskLevel, RiskProfile, ChangedFile

logger = logging.getLogger(__name__)

# Risk indicators — heuristic patterns in file paths/names
_RISK_PATTERNS = {
    "auth": [
        r"auth",
        r"permission",
        r"security",
        r"credential",
        r"login",
        r"jwt",
        r"oauth",
        r"session",
    ],
    "payment": [
        r"payment",
        r"billing",
        r"transaction",
        r"charge",
        r"subscription",
    ],
    "database": [
        r"migration",
        r"schema",
        r"database",
        r"db\.",
        r"query",
        r"model\.py",
    ],
    "api": [
        r"controller",
        r"route",
        r"endpoint",
        r"api",
        r"handler",
        r"view\.py",
    ],
}


def _classify_file_risk(file_path: str) -> tuple[RiskLevel, list[str]]:
    """Classify file risk based on path heuristics (doc section 3)."""
    reasons = []
    risk_level = RiskLevel.LOW

    # Check for test files — lower risk
    if "test" in file_path.lower() or "spec" in file_path.lower():
        reasons.append("Test file")
        return RiskLevel.LOW, reasons

    # Check for docs/README
    if file_path.endswith((".md", ".txt", ".rst")):
        reasons.append("Documentation file")
        return RiskLevel.LOW, reasons

    # Check for high-risk patterns
    for category, patterns in _RISK_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, file_path, re.IGNORECASE):
                if category in ("auth", "payment", "database"):
                    risk_level = RiskLevel.HIGH
                    reasons.append(f"Touches {category}")
                elif category == "api":
                    if risk_level != RiskLevel.HIGH:
                        risk_level = RiskLevel.MEDIUM
                    reasons.append(f"Public API/controller")
                break

    if risk_level == RiskLevel.LOW and not reasons:
        reasons.append("Regular code change")

    return risk_level, reasons


def _extract_changed_lines(patch: str) -> set[int]:
    """Extract changed line numbers from unified diff patch."""
    changed_lines = set()
    current_line = 0
    for line in patch.split("\n"):
        if line.startswith("@@"):
            # Parse line numbers: @@ -10,5 +12,6 @@
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or 1)
                current_line = start
        elif line.startswith("+") and not line.startswith("+++"):
            changed_lines.add(current_line)
            current_line += 1
        elif not line.startswith("-"):
            current_line += 1
    return changed_lines


def analyze_diff(changed_files: list[dict]) -> tuple[dict[str, RiskProfile], dict[str, set[int]]]:
    """Analyze PR diff and produce risk profiles + changed line maps.

    Args:
        changed_files: Output from GitHub API /pulls/{n}/files

    Returns:
        Tuple of:
        - risk_profiles: {file_path: RiskProfile}
        - changed_line_map: {file_path: set of changed line numbers}
    """
    risk_profiles = {}
    changed_line_map = {}

    for file_data in changed_files:
        file_path = file_data.get("filename", "")
        patch = file_data.get("patch", "")

        # Skip deleted files
        if file_data.get("status") == "removed":
            logger.debug("Skipping deleted file: %s", file_path)
            continue

        # Analyze risk
        risk_level, reasons = _classify_file_risk(file_path)
        risk_profiles[file_path] = RiskProfile(
            file=file_path,
            level=risk_level,
            reasons=reasons,
        )

        # Extract changed lines
        if patch:
            changed_lines = _extract_changed_lines(patch)
            changed_line_map[file_path] = changed_lines
            logger.debug(
                "File %s: risk=%s, changed_lines=%d",
                file_path,
                risk_level,
                len(changed_lines),
            )
        else:
            changed_line_map[file_path] = set()

    return risk_profiles, changed_line_map
