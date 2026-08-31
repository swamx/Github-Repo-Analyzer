"""Async GitHub REST client for PR review (doc section 2, 21–22)."""
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx

from app.config import settings
from app.services.resilient_client import ResilientClient

logger = logging.getLogger(__name__)

_GITHUB_API_HEADERS = {
    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _parse_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL or 'owner/repo' slug."""
    path = repo_url.rstrip("/").split("github.com/")[-1]
    owner, repo = path.split("/", 1)
    return owner, repo.removesuffix(".git")


class AsyncGitHubReviewClient:
    """GitHub REST API client for PR reviews, wrapped with resilience."""

    def __init__(self, resilient_client: Optional[ResilientClient] = None):
        self.api_base = settings.GITHUB_API_BASE.rstrip("/")
        self.resilient_client = resilient_client

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Fetch PR metadata including head commit SHA."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=15) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}"
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_pull_request_files(self, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """Fetch changed files with unified diff patches."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=30) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}/files"
            files = []
            page = 1
            while True:
                resp = await client.get(url, params={"per_page": 100, "page": page})
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                files.extend(batch)
                page += 1
            return files

    async def get_file_content(self, owner: str, repo: str, ref: str, file_path: str) -> str:
        """Fetch raw file content at a specific commit/ref."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=15) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/contents/{file_path}"
            resp = await client.get(url, params={"ref": ref})
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "content" in data:
                import base64
                return base64.b64decode(data["content"]).decode("utf-8")
            return str(data)

    async def post_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        file_path: str,
        line: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a line-anchored review comment on a PR."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=15) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
            payload = {
                "commit_id": commit_id,
                "path": file_path,
                "line": line,
                "body": body,
            }
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def post_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        """Post a summary comment on the PR issue."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=15) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/issues/{issue_number}/comments"
            resp = await client.post(url, json={"body": body})
            resp.raise_for_status()
            return resp.json()

    async def list_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """List existing comments on a PR (for context, dedup checks)."""
        async with httpx.AsyncClient(headers=_GITHUB_API_HEADERS, timeout=15) as client:
            url = f"{self.api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"
            resp = await client.get(url, params={"per_page": 100})
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
        """Verify GitHub webhook HMAC-SHA256 signature (doc section 21).

        Args:
            body: Raw request body bytes
            signature: X-Hub-Signature-256 header value (format: sha256=<hex>)
            secret: GITHUB_WEBHOOK_SECRET

        Returns:
            True if signature is valid, False otherwise
        """
        if not secret:
            logger.warning("verify_webhook_signature called but secret is empty")
            return False
        try:
            expected_hash = hmac.new(
                secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            # signature format: "sha256=<hash>"
            provided_hash = signature.replace("sha256=", "")
            return hmac.compare_digest(expected_hash, provided_hash)
        except Exception as e:
            logger.error("Webhook signature verification failed: %s", e)
            return False
