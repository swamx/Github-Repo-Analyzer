"""GitHub tool definitions and executors for the Claude API planner.

Two concerns are kept separate on purpose:
  TOOL_SCHEMAS  — JSON schemas passed to client.messages.create(tools=[...])
  TOOL_HANDLERS — plain Python functions that run when Claude calls a tool
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from issue_pilot.config import settings

logger = logging.getLogger(__name__)

_HEADERS = {
    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL or 'owner/repo' slug."""
    path = repo_url.rstrip("/").split("github.com/")[-1]
    owner, repo = path.split("/", 1)
    return owner, repo.removesuffix(".git")


# ── Anthropic tool schemas ────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "fetch_issue",
        "description": (
            "Fetch a single GitHub issue and return its title, body, labels, "
            "state, and all comments. Call this FIRST before writing any plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "Full GitHub repository URL, e.g. https://github.com/owner/repo",
                },
                "issue_number": {
                    "type": "integer",
                    "description": "The issue number to fetch.",
                },
            },
            "required": ["repo_url", "issue_number"],
        },
    },
    {
        "name": "get_default_branch",
        "description": "Return the repository's default branch name (e.g. 'main' or 'master').",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "Full GitHub repository URL.",
                },
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "post_issue_comment",
        "description": (
            "Post a comment on a GitHub issue. Use this after writing the fix plan "
            "to notify stakeholders directly on the issue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "Full GitHub repository URL.",
                },
                "issue_number": {
                    "type": "integer",
                    "description": "The issue number to comment on.",
                },
                "body": {
                    "type": "string",
                    "description": "Markdown body of the comment.",
                },
            },
            "required": ["repo_url", "issue_number", "body"],
        },
    },
]


# ── Python executors ──────────────────────────────────────────────────────────

def fetch_issue(repo_url: str, issue_number: int) -> dict[str, Any]:
    owner, repo = _parse_repo(repo_url)
    base = f"{settings.GITHUB_API_BASE}/repos/{owner}/{repo}"
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        issue = client.get(f"{base}/issues/{issue_number}").raise_for_status().json()
        comments = client.get(f"{base}/issues/{issue_number}/comments").raise_for_status().json()
    return {
        "number": issue["number"],
        "title": issue["title"],
        "body": issue.get("body", ""),
        "labels": [lb["name"] for lb in issue.get("labels", [])],
        "state": issue["state"],
        "comments": [c["body"] for c in comments],
    }


def get_default_branch(repo_url: str) -> str:
    owner, repo = _parse_repo(repo_url)
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        data = client.get(f"{settings.GITHUB_API_BASE}/repos/{owner}/{repo}").raise_for_status().json()
    return data["default_branch"]


def post_issue_comment(repo_url: str, issue_number: int, body: str) -> dict[str, str]:
    owner, repo = _parse_repo(repo_url)
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        resp = client.post(
            f"{settings.GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        ).raise_for_status().json()
    return {"html_url": resp["html_url"]}


# map tool name → executor so the agentic loop can dispatch generically
TOOL_HANDLERS: dict[str, Any] = {
    "fetch_issue": fetch_issue,
    "get_default_branch": get_default_branch,
    "post_issue_comment": post_issue_comment,
}


def create_branch(repo_url: str, branch_name: str, base_branch: str = "main") -> str:
    """Create branch off base_branch; returns the new branch name. (used by worker)"""
    owner, repo = _parse_repo(repo_url)
    base = f"{settings.GITHUB_API_BASE}/repos/{owner}/{repo}"
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        ref_data = client.get(f"{base}/git/ref/heads/{base_branch}").raise_for_status().json()
        sha = ref_data["object"]["sha"]
        client.post(
            f"{base}/git/refs",
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        ).raise_for_status()
    logger.info("Branch '%s' created from '%s'", branch_name, base_branch)
    return branch_name


def create_pull_request(
    repo_url: str,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
) -> str:
    """Open a pull request; returns its HTML URL. (used by worker)"""
    owner, repo = _parse_repo(repo_url)
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        pr = client.post(
            f"{settings.GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": branch_name, "base": base_branch},
        ).raise_for_status().json()
    url: str = pr["html_url"]
    logger.info("PR created: %s", url)
    return url
