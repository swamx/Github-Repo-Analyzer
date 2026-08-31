"""Feedback store for developer reactions and metrics (doc section 15–16)."""
import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class FeedbackStore:
    """SQLite-based feedback store for PR review findings."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or settings.REVIEW_DB_PATH
        self._init_schema()

    def _init_schema(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pr_reviews (
                    id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    pull_request INTEGER NOT NULL,
                    head_commit TEXT NOT NULL,
                    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(repository, pull_request, head_commit)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL,
                    file TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    suggested_fix TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (review_id) REFERENCES pr_reviews(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL,
                    reaction TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (finding_id) REFERENCES findings(id)
                )
            """)
            conn.commit()

    def _make_review_id(self, repository: str, pr_number: int, head_commit: str) -> str:
        """Generate unique review ID from repo + PR + commit."""
        key = f"{repository}#{pr_number}#{head_commit}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _make_finding_id(self, review_id: str, file: str, line: int, category: str) -> str:
        """Generate unique finding ID."""
        key = f"{review_id}#{file}#{line}#{category}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def store_review(
        self,
        repository: str,
        pr_number: int,
        head_commit: str,
        findings: list[dict],
    ) -> str:
        """Store a PR review and its findings (doc section 16 online eval).

        Args:
            repository: owner/repo
            pr_number: PR number
            head_commit: Head commit SHA
            findings: List of {file, line, category, severity, confidence, suggested_fix}

        Returns:
            Review ID (for feedback lookup)
        """
        review_id = self._make_review_id(repository, pr_number, head_commit)

        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pr_reviews (id, repository, pull_request, head_commit)
                    VALUES (?, ?, ?, ?)
                    """,
                    (review_id, repository, pr_number, head_commit),
                )

                for finding in findings:
                    finding_id = self._make_finding_id(
                        review_id,
                        finding["file"],
                        finding["line"],
                        finding["category"],
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO findings
                        (id, review_id, file, line, category, severity, confidence, suggested_fix)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            finding_id,
                            review_id,
                            finding["file"],
                            finding["line"],
                            finding["category"],
                            finding["severity"],
                            finding["confidence"],
                            finding.get("suggested_fix", ""),
                        ),
                    )

                conn.commit()
                logger.info("Stored review %s with %d findings", review_id, len(findings))
            except sqlite3.IntegrityError:
                logger.info("Review %s already stored (idempotent)", review_id)

        return review_id

    def record_feedback(self, finding_id: str, reaction: str) -> bool:
        """Record developer feedback on a finding (doc section 15).

        Args:
            finding_id: Finding ID (from store_review)
            reaction: helpful, not_helpful, false_positive, already_known, fixed

        Returns:
            True if recorded, False if finding_id not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM findings WHERE id = ?",
                (finding_id,),
            )
            if not cursor.fetchone():
                logger.warning("Feedback: finding_id %s not found", finding_id)
                return False

            conn.execute(
                "INSERT INTO feedback (finding_id, reaction) VALUES (?, ?)",
                (finding_id, reaction),
            )
            conn.commit()
            logger.info("Recorded feedback: %s → %s", finding_id, reaction)
            return True

    def get_stats(self, repository: Optional[str] = None) -> dict:
        """Get aggregated feedback stats (doc section 16 online eval).

        Args:
            repository: Filter by repository (optional)

        Returns:
            {
                'total_reviews': N,
                'total_findings': N,
                'findings_by_category': {...},
                'feedback_by_reaction': {...},
                'false_positive_rate': 0.XX,
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total reviews
            if repository:
                total_reviews = conn.execute(
                    "SELECT COUNT(*) as cnt FROM pr_reviews WHERE repository = ?",
                    (repository,),
                ).fetchone()["cnt"]
            else:
                total_reviews = conn.execute(
                    "SELECT COUNT(*) as cnt FROM pr_reviews"
                ).fetchone()["cnt"]

            # Total findings
            if repository:
                rows = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM findings f
                    JOIN pr_reviews r ON f.review_id = r.id
                    WHERE r.repository = ?
                    """,
                    (repository,),
                ).fetchone()
            else:
                rows = conn.execute(
                    "SELECT COUNT(*) as cnt FROM findings"
                ).fetchone()
            total_findings = rows["cnt"] if rows else 0

            # By category
            if repository:
                category_rows = conn.execute(
                    """
                    SELECT f.category, COUNT(*) as cnt FROM findings f
                    JOIN pr_reviews r ON f.review_id = r.id
                    WHERE r.repository = ?
                    GROUP BY f.category
                    """,
                    (repository,),
                ).fetchall()
            else:
                category_rows = conn.execute(
                    """
                    SELECT category, COUNT(*) as cnt FROM findings
                    GROUP BY category
                    """
                ).fetchall()

            by_category = {row["category"]: row["cnt"] for row in category_rows}

            # Feedback aggregation
            feedback_rows = conn.execute(
                "SELECT reaction, COUNT(*) as cnt FROM feedback GROUP BY reaction"
            ).fetchall()
            by_reaction = {row["reaction"]: row["cnt"] for row in feedback_rows}

            # False positive rate
            fp_count = by_reaction.get("false_positive", 0)
            fp_rate = (fp_count / total_findings) if total_findings > 0 else 0.0

            return {
                "total_reviews": total_reviews,
                "total_findings": total_findings,
                "findings_by_category": by_category,
                "feedback_by_reaction": by_reaction,
                "false_positive_rate": round(fp_rate, 3),
            }
