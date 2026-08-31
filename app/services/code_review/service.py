"""Main code review service orchestrating the full pipeline."""
import asyncio
import logging
from typing import Optional

from app.services.code_review.github_client import AsyncGitHubReviewClient, _parse_repo
from app.services.code_review.diff_analyzer import analyze_diff
from app.services.code_review.orchestrator import ReviewOrchestrator
from app.services.code_review.agents import (
    run_correctness_agent,
    run_security_agent,
    run_test_agent,
)
from app.services.code_review.validation import validate_findings
from app.services.code_review.dedup import deduplicate_findings
from app.services.code_review.scoring import score_findings, filter_by_comment_level
from app.services.code_review.comment_formatter import (
    format_finding_comment,
    format_summary_comment,
)
from app.services.code_review.feedback_store import FeedbackStore
from app.services.code_review.schemas import (
    ReviewResult,
    RiskLevel,
    CommentLevel,
    Finding,
)

logger = logging.getLogger(__name__)


class CodeReviewService:
    """Orchestrates the full code review pipeline (doc sections 2–16)."""

    def __init__(self):
        self.github_client = AsyncGitHubReviewClient()
        self.orchestrator = ReviewOrchestrator()
        self.feedback_store = FeedbackStore()

    async def review_pull_request(
        self,
        repo: str,
        pr_number: int,
        dry_run: bool = False,
        info_threshold: Optional[float] = None,
        block_threshold: Optional[float] = None,
    ) -> ReviewResult:
        """Main entry point: run the full review pipeline (doc sections 2–16).

        Args:
            repo: owner/repo or full GitHub URL
            pr_number: PR number
            dry_run: Don't post to GitHub if True
            info_threshold: Confidence threshold for informational comments (optional)
            block_threshold: Confidence threshold for blocking comments (optional)

        Returns:
            ReviewResult with findings and summary
        """
        logger.info("Starting review: %s PR#%d dry_run=%s", repo, pr_number, dry_run)

        try:
            owner, repo_name = _parse_repo(repo)
        except ValueError as e:
            logger.error("Invalid repo format: %s", e)
            raise ValueError(f"Invalid repo format: {e}") from e

        # 1. Fetch PR metadata and files (doc section 2)
        try:
            pr_data = await self.github_client.get_pull_request(owner, repo_name, pr_number)
            head_commit = pr_data["head"]["sha"]
            changed_files = await self.github_client.get_pull_request_files(owner, repo_name, pr_number)
            logger.info("Fetched PR#%d: %d changed files, head_commit=%s", pr_number, len(changed_files), head_commit[:8])
        except Exception as e:
            logger.error("Failed to fetch PR: %s", e)
            raise RuntimeError(f"Failed to fetch PR data: {e}") from e

        # 2. Deterministic diff analysis (doc section 3)
        risk_profiles, changed_line_map = analyze_diff(changed_files)
        changed_files_map = {
            f["filename"]: f.get("patch", "") for f in changed_files
        }

        # 3. Determine which agents to run (doc section 6)
        file_agents = self.orchestrator.plan_review(risk_profiles)

        # 4. Run agents in parallel (doc section 7)
        all_findings = []
        agent_tasks = []

        for file_path, agents in file_agents.items():
            patch = changed_files_map.get(file_path, "")
            if not patch:
                logger.debug("Skipping %s: no patch", file_path)
                continue

            # Build context (lightweight, no vector DB)
            related_content = {}
            for cf in changed_files:
                if cf["filename"] != file_path and "test" in cf["filename"].lower():
                    related_content[cf["filename"]] = cf.get("patch", "")[:500]

            for agent_name in agents:
                if agent_name == "correctness":
                    agent_tasks.append(
                        run_correctness_agent(file_path, patch, related_content)
                    )
                elif agent_name == "security":
                    agent_tasks.append(
                        run_security_agent(file_path, patch, related_content)
                    )
                elif agent_name == "testing":
                    agent_tasks.append(
                        run_test_agent(file_path, patch, related_content)
                    )

        if agent_tasks:
            agent_results = await asyncio.gather(*agent_tasks, return_exceptions=False)
            for result in agent_results:
                if isinstance(result, list):
                    all_findings.extend(result)
            logger.info("Agents produced %d raw findings", len(all_findings))

        # 5. Validation: treat LLM output as untrusted (doc section 10)
        validated, dropped = validate_findings(all_findings, changed_files_map, changed_line_map)
        if dropped:
            logger.info("Dropped %d findings during validation", len(dropped))

        # 6. Deduplication (doc section 11)
        deduped = deduplicate_findings(validated)
        logger.info("After dedup: %d findings", len(deduped))

        # 7. Confidence/severity scoring (doc section 12–13)
        scored, severity_summary = score_findings(
            deduped,
            info_threshold=info_threshold,
            block_threshold=block_threshold,
        )

        # 8. Filter by comment level
        informational_findings = filter_by_comment_level(scored, CommentLevel.INFORMATIONAL)
        blocking_findings = [
            f for f in informational_findings
            if getattr(f, "_comment_level", CommentLevel.INFORMATIONAL) == CommentLevel.BLOCKING
        ]

        # 9. Build result
        result = ReviewResult(
            repository=f"{owner}/{repo_name}",
            pull_request=pr_number,
            head_commit=head_commit,
            status="completed",
            findings=informational_findings,
            risk_summary=severity_summary,
            comment_level_summary={
                "blocking": len(blocking_findings),
                "informational": len(informational_findings) - len(blocking_findings),
            },
        )

        # 10. Post to GitHub (doc section 14) unless dry_run
        if not dry_run and informational_findings:
            try:
                await self._post_findings_to_github(
                    owner,
                    repo_name,
                    pr_number,
                    head_commit,
                    informational_findings,
                    blocking_findings,
                )
            except Exception as e:
                logger.error("Failed to post findings to GitHub: %s", e)
                # Don't fail the review if posting fails

        # 11. Store review + findings for metrics (doc section 16)
        try:
            finding_dicts = [
                {
                    "file": f.file,
                    "line": f.line,
                    "category": f.category.value,
                    "severity": f.severity.value,
                    "confidence": f.confidence,
                    "suggested_fix": f.suggested_fix,
                }
                for f in informational_findings
            ]
            self.feedback_store.store_review(
                f"{owner}/{repo_name}",
                pr_number,
                head_commit,
                finding_dicts,
            )
        except Exception as e:
            logger.warning("Failed to store review in feedback store: %s", e)

        logger.info(
            "Review complete: %d findings, %d blocking, %d informational",
            len(informational_findings),
            len(blocking_findings),
            len(informational_findings) - len(blocking_findings),
        )
        return result

    async def _post_findings_to_github(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        head_commit: str,
        all_findings: list[Finding],
        blocking_findings: list[Finding],
    ) -> None:
        """Post findings as GitHub PR comments (doc section 14)."""
        logger.info("Posting %d findings to GitHub PR#%d", len(all_findings), pr_number)

        # Post individual line comments for each finding
        for finding in all_findings:
            try:
                comment_body = format_finding_comment(finding)
                await self.github_client.post_review_comment(
                    owner,
                    repo,
                    pr_number,
                    head_commit,
                    finding.file,
                    finding.line,
                    comment_body,
                )
                logger.debug("Posted comment for %s:%d", finding.file, finding.line)
            except Exception as e:
                logger.warning("Failed to post comment for %s:%d: %s", finding.file, finding.line, e)

        # Post summary comment
        try:
            summary_body = format_summary_comment(all_findings, blocking_findings)
            await self.github_client.post_issue_comment(owner, repo, pr_number, summary_body)
            logger.info("Posted summary comment to PR#%d", pr_number)
        except Exception as e:
            logger.warning("Failed to post summary comment: %s", e)
