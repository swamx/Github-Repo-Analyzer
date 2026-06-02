import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests

from app.config import settings
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class GitHubService:
    """Service for fetching GitHub repository data via GraphQL API"""

    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self):
        self.cache = CacheService()
        self.headers = {
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "Content-Type": "application/json",
        }

    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        """Extract owner and repo from GitHub URL or owner/repo shorthand."""
        url = repo_url.strip().rstrip("/")
        if url.startswith("http"):
            parts = url.split("/")
            if len(parts) < 2:
                raise ValueError(f"Cannot parse repository URL: {repo_url}")
            owner, repo = parts[-2], parts[-1]
        else:
            parts = url.split("/")
            if len(parts) != 2:
                raise ValueError(f"Expected 'owner/repo' format, got: {repo_url}")
            owner, repo = parts[0], parts[1]
        logger.debug("_parse_repo_url owner=%s repo=%s", owner, repo)
        return owner, repo

    def fetch_repository_data(
        self,
        owner: str,
        repo: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        time_key = (
            f"{start_time.isoformat() if start_time else 'all'}"
            f"_{end_time.isoformat() if end_time else 'all'}"
        )
        cache_key = f"github:{owner}:{repo}:snapshot:{time_key}"

        cached = self.cache.get(cache_key)
        if cached:
            logger.debug("fetch_repository_data cache hit owner=%s repo=%s", owner, repo)
            return cached

        logger.info("fetch_repository_data owner=%s repo=%s start=%s end=%s", owner, repo, start_time, end_time)

        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            nameWithOwner
            description
            url
            pushedAt

            pullRequests(
              first: 100,
              states: MERGED,
              orderBy: { field: UPDATED_AT, direction: DESC }
            ) {
              pageInfo { hasNextPage endCursor }
              nodes {
                number title createdAt mergedAt additions deletions changedFiles
                author { login url }
                reviews(first: 50) {
                  nodes {
                    createdAt state submittedAt
                    author { login url }
                  }
                }
                commits(first: 1) {
                  nodes { commit { committedDate } }
                }
              }
            }

            issues(
              first: 100,
              states: CLOSED,
              orderBy: { field: UPDATED_AT, direction: DESC }
            ) {
              pageInfo { hasNextPage endCursor }
              nodes {
                number title createdAt closedAt
                comments { totalCount }
                author { login url }
                labels(first: 10) { nodes { name } }
              }
            }
          }
        }
        """

        variables = {"owner": owner, "repo": repo}

        try:
            response = requests.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                logger.error("GraphQL errors owner=%s repo=%s errors=%s", owner, repo, data["errors"])
                raise ValueError(f"GraphQL error: {data['errors']}")

            self.cache.set(cache_key, data)
            logger.info("fetch_repository_data completed owner=%s repo=%s", owner, repo)
            return data

        except requests.exceptions.RequestException as e:
            logger.error("fetch_repository_data HTTP error owner=%s repo=%s: %s", owner, repo, e)
            raise RuntimeError(f"Failed to fetch GitHub data: {e}") from e

    def get_commit_history(
        self,
        owner: str,
        repo: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        since = start_time.isoformat() if start_time else None
        until = end_time.isoformat() if end_time else None

        logger.info("get_commit_history owner=%s repo=%s since=%s until=%s", owner, repo, since, until)

        query = """
        query($owner: String!, $repo: String!, $since: GitTimestamp, $until: GitTimestamp) {
          repository(owner: $owner, name: $repo) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(first: 50, since: $since, until: $until) {
                    nodes {
                      oid committedDate message
                      author { name email date }
                      authoredByCommitter
                    }
                  }
                }
              }
            }
          }
        }
        """

        variables = {"owner": owner, "repo": repo, "since": since, "until": until}

        try:
            response = requests.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error("get_commit_history HTTP error owner=%s repo=%s: %s", owner, repo, e)
            raise RuntimeError(f"Failed to fetch commit history: {e}") from e
