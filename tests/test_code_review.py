"""Tests for the code review pipeline (unit + integration)."""
import asyncio
import json
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from app.services.code_review.schemas import (
    Finding,
    FindingCategory,
    Severity,
    RiskLevel,
    RiskProfile,
)
from app.services.code_review.diff_analyzer import analyze_diff, _classify_file_risk
from app.services.code_review.validation import validate_findings
from app.services.code_review.dedup import deduplicate_findings
from app.services.code_review.scoring import score_findings
from app.services.code_review.github_client import AsyncGitHubReviewClient
from app.services.code_review.feedback_store import FeedbackStore


# ============================================================================
# Unit Tests: diff_analyzer
# ============================================================================


class TestDiffAnalyzer:
    """Test deterministic diff analysis (doc section 3)."""

    def test_classify_file_risk_auth(self):
        """Auth-related files should be HIGH risk."""
        level, reasons = _classify_file_risk("src/auth/login.py")
        assert level == RiskLevel.HIGH
        assert any("auth" in r.lower() for r in reasons)

    def test_classify_file_risk_payment(self):
        """Payment files should be HIGH risk."""
        level, reasons = _classify_file_risk("src/payments/processor.py")
        assert level == RiskLevel.HIGH
        assert any("payment" in r.lower() for r in reasons)

    def test_classify_file_risk_test(self):
        """Test files should be LOW risk."""
        level, reasons = _classify_file_risk("tests/test_auth.py")
        assert level == RiskLevel.LOW
        assert any("test" in r.lower() for r in reasons)

    def test_classify_file_risk_docs(self):
        """Docs should be LOW risk."""
        level, reasons = _classify_file_risk("README.md")
        assert level == RiskLevel.LOW
        assert any("documentation" in r.lower() for r in reasons)

    def test_classify_file_risk_regular(self):
        """Regular utility files should be LOW risk."""
        level, reasons = _classify_file_risk("src/utils/helpers.py")
        assert level == RiskLevel.LOW

    def test_analyze_diff(self):
        """Test diff analysis with sample changed files."""
        changed_files = [
            {
                "filename": "src/auth/login.py",
                "status": "modified",
                "patch": """@@ -10,5 +10,6 @@
 def login(user, password):
     # Check password
-    if not password:
+    # Removed check
     authenticate(user)""",
            },
            {
                "filename": "README.md",
                "status": "modified",
                "patch": """@@ -1 +1 @@
-# Old title
+# New title""",
            },
            {
                "filename": "old_file.txt",
                "status": "removed",
                "patch": "",
            },
        ]

        risk_profiles, changed_line_map = analyze_diff(changed_files)

        assert len(risk_profiles) == 2  # Deleted file skipped
        assert risk_profiles["src/auth/login.py"].level == RiskLevel.HIGH
        assert risk_profiles["README.md"].level == RiskLevel.LOW
        assert len(changed_line_map["src/auth/login.py"]) > 0


# ============================================================================
# Unit Tests: validation
# ============================================================================


class TestValidation:
    """Test finding validation (doc section 10)."""

    def test_validate_findings_valid(self):
        """Valid findings should pass."""
        finding = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.8,
            evidence=["bug line"],
        )

        validated, dropped = validate_findings(
            [finding],
            changed_files={"src/app.py": "line 10: bug line\n"},
            changed_line_map={"src/app.py": {10}},
        )

        assert len(validated) == 1
        assert len(dropped) == 0

    def test_validate_findings_invalid_file(self):
        """Findings for non-changed files should be dropped."""
        finding = Finding(
            file="src/other.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.8,
        )

        validated, dropped = validate_findings(
            [finding],
            changed_files={"src/app.py": ""},
            changed_line_map={"src/app.py": set()},
        )

        assert len(validated) == 0
        assert len(dropped) == 1

    def test_validate_findings_invalid_line(self):
        """Findings on non-changed lines should be dropped."""
        finding = Finding(
            file="src/app.py",
            line=999,  # Not changed
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.8,
        )

        validated, dropped = validate_findings(
            [finding],
            changed_files={"src/app.py": "some code"},
            changed_line_map={"src/app.py": {10, 11}},  # Only 10–11 changed
        )

        assert len(validated) == 0
        assert len(dropped) == 1


# ============================================================================
# Unit Tests: deduplication
# ============================================================================


class TestDeduplication:
    """Test finding deduplication (doc section 11)."""

    def test_dedup_identical_findings(self):
        """Identical findings should merge."""
        finding1 = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.HIGH,
            confidence=0.9,
            suggested_fix="Fix this",
        )
        finding2 = Finding(
            file="src/app.py",
            line=11,  # Nearby line
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.7,
            suggested_fix="Fix this",
        )

        deduped = deduplicate_findings([finding1, finding2])

        # Should merge to one
        assert len(deduped) == 1
        # Keep highest confidence
        assert deduped[0].confidence == 0.9

    def test_dedup_different_findings(self):
        """Different findings should remain separate."""
        finding1 = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.HIGH,
            confidence=0.9,
            suggested_fix="Fix A",
        )
        finding2 = Finding(
            file="src/app.py",
            line=100,  # Far away
            category=FindingCategory.SECURITY,
            severity=Severity.CRITICAL,
            confidence=0.85,
            suggested_fix="Fix B",
        )

        deduped = deduplicate_findings([finding1, finding2])

        assert len(deduped) == 2


# ============================================================================
# Unit Tests: scoring
# ============================================================================


class TestScoring:
    """Test confidence/severity scoring (doc section 12–13)."""

    def test_score_findings_suppressed(self):
        """Low-confidence findings should be suppressed."""
        finding = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.5,  # Below 0.65 threshold
        )

        scored, summary = score_findings([finding], info_threshold=0.65, block_threshold=0.85)

        # Should be marked as suppressed
        assert getattr(scored[0], "_comment_level").value == "suppressed"

    def test_score_findings_informational(self):
        """Medium-confidence findings should be informational."""
        finding = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.MEDIUM,
            confidence=0.7,  # Between thresholds
        )

        scored, summary = score_findings([finding], info_threshold=0.65, block_threshold=0.85)

        assert getattr(scored[0], "_comment_level").value == "informational"

    def test_score_findings_blocking(self):
        """High-confidence critical findings should be blocking."""
        finding = Finding(
            file="src/app.py",
            line=10,
            category=FindingCategory.SECURITY,
            severity=Severity.CRITICAL,
            confidence=0.9,  # Above 0.85 threshold
        )

        scored, summary = score_findings([finding], info_threshold=0.65, block_threshold=0.85)

        assert getattr(scored[0], "_comment_level").value == "blocking"


# ============================================================================
# Unit Tests: webhook signature verification
# ============================================================================


class TestWebhookSignature:
    """Test GitHub webhook HMAC verification (doc section 21)."""

    def test_verify_webhook_signature_valid(self):
        """Valid signature should be accepted."""
        secret = "test-secret"
        body = b'{"action": "opened"}'
        expected_hash = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        signature = f"sha256={expected_hash}"

        valid = AsyncGitHubReviewClient.verify_webhook_signature(body, signature, secret)
        assert valid is True

    def test_verify_webhook_signature_invalid(self):
        """Invalid signature should be rejected."""
        secret = "test-secret"
        body = b'{"action": "opened"}'
        bad_signature = "sha256=badhash1234567890"

        valid = AsyncGitHubReviewClient.verify_webhook_signature(body, bad_signature, secret)
        assert valid is False

    def test_verify_webhook_signature_empty_secret(self):
        """Empty secret should be rejected."""
        body = b'{"action": "opened"}'
        signature = "sha256=anything"

        valid = AsyncGitHubReviewClient.verify_webhook_signature(body, signature, "")
        assert valid is False


# ============================================================================
# Integration Tests: API endpoints
# ============================================================================


class TestCodeReviewRoutes:
    """Test code review API routes."""

    def test_trigger_review_invalid_repo(self):
        """Invalid repo format should return 400."""
        client = TestClient(app)
        response = client.post(
            "/api/code-review/review",
            json={"repo": "invalid", "pr_number": 1},
        )
        assert response.status_code == 400

    @patch("app.api.code_review_routes._review_service")
    def test_trigger_review_success(self, mock_service):
        """Valid request should trigger review."""
        mock_result = MagicMock()
        mock_result.findings = []
        mock_result.risk_summary = {}
        mock_result.comment_level_summary = {}
        mock_service.review_pull_request = AsyncMock(return_value=mock_result)

        client = TestClient(app)
        response = client.post(
            "/api/code-review/review",
            json={"repo": "owner/repo", "pr_number": 1, "dry_run": True},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    def test_webhook_missing_signature(self):
        """Webhook without signature should return 401."""
        client = TestClient(app)
        response = client.post(
            "/api/code-review/webhook",
            json={"action": "opened"},
        )
        assert response.status_code == 401 or response.status_code == 501  # Depends on secret config

    def test_webhook_bad_signature(self):
        """Webhook with bad signature should return 401."""
        with patch("app.config.settings.GITHUB_WEBHOOK_SECRET", "secret"):
            client = TestClient(app)
            response = client.post(
                "/api/code-review/webhook",
                json={"action": "opened"},
                headers={"X-Hub-Signature-256": "sha256=badsignature"},
            )
            assert response.status_code == 401

    @patch("app.api.code_review_routes._feedback_store")
    def test_feedback_valid(self, mock_store):
        """Valid feedback should be recorded."""
        mock_store.record_feedback.return_value = True

        client = TestClient(app)
        response = client.post(
            "/api/code-review/feedback",
            json={"finding_id": "finding123", "reaction": "helpful"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "recorded"

    @patch("app.api.code_review_routes._feedback_store")
    def test_feedback_invalid_reaction(self, mock_store):
        """Invalid reaction should return 400."""
        client = TestClient(app)
        response = client.post(
            "/api/code-review/feedback",
            json={"finding_id": "finding123", "reaction": "invalid_reaction"},
        )

        assert response.status_code == 400

    @patch("app.api.code_review_routes._feedback_store")
    def test_stats(self, mock_store):
        """Stats endpoint should return metrics."""
        mock_store.get_stats.return_value = {
            "total_reviews": 10,
            "total_findings": 25,
            "false_positive_rate": 0.1,
        }

        client = TestClient(app)
        response = client.get("/api/code-review/stats")

        assert response.status_code == 200
        assert response.json()["data"]["total_reviews"] == 10


# ============================================================================
# Integration Tests: FeedbackStore
# ============================================================================


class TestFeedbackStore:
    """Test feedback storage and metrics."""

    def test_store_review_and_feedback(self, tmp_path):
        """Store a review and record feedback."""
        db_path = str(tmp_path / "test.db")
        store = FeedbackStore(db_path=db_path)

        # Store a review
        findings = [
            {
                "file": "src/app.py",
                "line": 10,
                "category": "correctness",
                "severity": "medium",
                "confidence": 0.8,
                "suggested_fix": "Fix this",
            }
        ]
        review_id = store.store_review("owner/repo", 1, "abc123", findings)

        # Verify review was stored
        stats = store.get_stats()
        assert stats["total_reviews"] == 1
        assert stats["total_findings"] == 1

    def test_false_positive_rate(self, tmp_path):
        """FP rate should be calculated correctly."""
        db_path = str(tmp_path / "test.db")
        store = FeedbackStore(db_path=db_path)

        findings = [
            {
                "file": "src/app.py",
                "line": i,
                "category": "correctness",
                "severity": "medium",
                "confidence": 0.8,
            }
            for i in range(10)
        ]
        review_id = store.store_review("owner/repo", 1, "abc123", findings)

        # Stats should reflect stored review
        stats = store.get_stats()
        assert stats["total_reviews"] == 1
        assert stats["total_findings"] == 10
        assert stats["false_positive_rate"] == 0.0  # No feedback recorded yet
