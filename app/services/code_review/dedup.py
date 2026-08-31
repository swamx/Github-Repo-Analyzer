"""Deduplication layer — merge near-duplicate findings (doc section 11)."""
import difflib
import logging
from collections import defaultdict
from typing import Optional

from app.services.code_review.schemas import Finding

logger = logging.getLogger(__name__)


def _text_similarity(a: str, b: str) -> float:
    """Compute token-overlap similarity (0.0–1.0)."""
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def _lines_overlap(line1: int, line2: int, tolerance: int = 3) -> bool:
    """Check if two line numbers are within tolerance (likely same issue)."""
    return abs(line1 - line2) <= tolerance


def deduplicate_findings(findings: list[Finding], similarity_threshold: float = 0.6) -> list[Finding]:
    """Merge near-duplicate findings (doc section 11).

    Groups by (file, overlapping line range, category) and text similarity,
    merging into the finding with highest confidence.

    Args:
        findings: List of validated findings
        similarity_threshold: 0.0–1.0 for text similarity matching

    Returns:
        Deduplicated findings
    """
    if not findings:
        return []

    # Group by file and category
    groups = defaultdict(list)
    for finding in findings:
        key = (finding.file, finding.category)
        groups[key].append(finding)

    merged = []

    for (file_path, category), group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Sort by line number for processing
        group.sort(key=lambda f: f.line)

        # Cluster by line proximity + text similarity
        clusters = []
        current_cluster = [group[0]]

        for finding in group[1:]:
            # Check if it overlaps with any in current cluster
            overlaps = any(
                _lines_overlap(finding.line, f.line) for f in current_cluster
            )
            # Check text similarity with best match in cluster
            best_similarity = max(
                _text_similarity(finding.suggested_fix, f.suggested_fix)
                for f in current_cluster
            )
            if overlaps or best_similarity >= similarity_threshold:
                current_cluster.append(finding)
            else:
                # Start new cluster
                clusters.append(current_cluster)
                current_cluster = [finding]

        clusters.append(current_cluster)

        # Merge each cluster, keeping highest confidence
        for cluster in clusters:
            best = max(cluster, key=lambda f: f.confidence)
            # Preserve evidence/notes from lower-confidence ones
            all_evidence = []
            for f in cluster:
                all_evidence.extend(f.evidence)
            best_evidence = list(dict.fromkeys(all_evidence))  # deduplicate
            merged_finding = best.model_copy(
                update={"evidence": best_evidence}
            )
            merged.append(merged_finding)
            if len(cluster) > 1:
                logger.info(
                    "Deduplicated %d similar findings in %s:%d",
                    len(cluster),
                    file_path,
                    best.line,
                )

    return merged
