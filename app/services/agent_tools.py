"""LangGraph agent tools for repository analysis"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.models.schemas import RepositoryMetrics
from app.services.analytics_service import AnalyticsService
from app.services.github_service import GitHubService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_github_service = GitHubService()
_analytics_service = AnalyticsService()
_llm_service = LLMService()


def fetch_repository_stats(
    repo_url: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch statistics for a GitHub repository."""
    logger.debug("fetch_repository_stats repo_url=%s start=%s end=%s", repo_url, start_time, end_time)
    try:
        owner, repo = _github_service._parse_repo_url(repo_url)
        start_dt = datetime.fromisoformat(start_time) if start_time else None
        end_dt = datetime.fromisoformat(end_time) if end_time else None

        github_data = _github_service.fetch_repository_data(
            owner=owner, repo=repo, start_time=start_dt, end_time=end_dt,
        )
        metrics = _analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=start_dt, end_time=end_dt,
        )
        logger.info("fetch_repository_stats success owner=%s repo=%s", owner, repo)
        return {"status": "success", "metrics": metrics.model_dump()}
    except Exception as e:
        logger.error("fetch_repository_stats failed repo_url=%s: %s", repo_url, e)
        return {"status": "error", "error": str(e)}


def analyze_metrics(
    repo_url: str,
    question: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze repository metrics and answer a question."""
    logger.debug("analyze_metrics repo_url=%s question=%s", repo_url, question)
    try:
        owner, repo = _github_service._parse_repo_url(repo_url)
        start_dt = datetime.fromisoformat(start_time) if start_time else None
        end_dt = datetime.fromisoformat(end_time) if end_time else None

        github_data = _github_service.fetch_repository_data(
            owner=owner, repo=repo, start_time=start_dt, end_time=end_dt,
        )
        metrics = _analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=start_dt, end_time=end_dt,
        )
        analysis = _llm_service.analyze_metrics(metrics, question)
        logger.info("analyze_metrics success owner=%s repo=%s", owner, repo)
        return {
            "status": "success",
            "analysis": analysis,
            "metrics_summary": {
                "avg_cycle_time": metrics.avg_cycle_time_hours,
                "avg_review_latency": metrics.avg_review_latency_hours,
                "quality_score": metrics.quality_score,
                "velocity_trend": metrics.velocity_trend,
            },
        }
    except Exception as e:
        logger.error("analyze_metrics failed repo_url=%s: %s", repo_url, e)
        return {"status": "error", "error": str(e)}


def generate_summary(
    repo_url: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate comprehensive summary for a repository."""
    logger.debug("generate_summary repo_url=%s", repo_url)
    try:
        owner, repo = _github_service._parse_repo_url(repo_url)
        start_dt = datetime.fromisoformat(start_time) if start_time else None
        end_dt = datetime.fromisoformat(end_time) if end_time else None

        github_data = _github_service.fetch_repository_data(
            owner=owner, repo=repo, start_time=start_dt, end_time=end_dt,
        )
        metrics = _analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=start_dt, end_time=end_dt,
        )
        analysis = _llm_service.summarize_metrics(metrics)
        logger.info("generate_summary success owner=%s repo=%s", owner, repo)
        return {"status": "success", "summary": analysis.model_dump()}
    except Exception as e:
        logger.error("generate_summary failed repo_url=%s: %s", repo_url, e)
        return {"status": "error", "error": str(e)}


def get_top_contributors(
    repo_url: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Get top contributors for a repository."""
    logger.debug("get_top_contributors repo_url=%s limit=%d", repo_url, limit)
    try:
        owner, repo = _github_service._parse_repo_url(repo_url)
        start_dt = datetime.fromisoformat(start_time) if start_time else None
        end_dt = datetime.fromisoformat(end_time) if end_time else None

        github_data = _github_service.fetch_repository_data(
            owner=owner, repo=repo, start_time=start_dt, end_time=end_dt,
        )
        metrics = _analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=start_dt, end_time=end_dt,
        )
        contributors = [eng.model_dump() for eng in metrics.top_contributors[:limit]]
        logger.info("get_top_contributors success owner=%s repo=%s count=%d", owner, repo, len(contributors))
        return {"status": "success", "contributors": contributors, "count": len(contributors)}
    except Exception as e:
        logger.error("get_top_contributors failed repo_url=%s: %s", repo_url, e)
        return {"status": "error", "error": str(e)}


def get_performance_trends(
    repo_url: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Get performance trends for a repository."""
    logger.debug("get_performance_trends repo_url=%s", repo_url)
    try:
        owner, repo = _github_service._parse_repo_url(repo_url)
        start_dt = datetime.fromisoformat(start_time) if start_time else None
        end_dt = datetime.fromisoformat(end_time) if end_time else None

        github_data = _github_service.fetch_repository_data(
            owner=owner, repo=repo, start_time=start_dt, end_time=end_dt,
        )
        metrics = _analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=start_dt, end_time=end_dt,
        )
        logger.info("get_performance_trends success owner=%s repo=%s", owner, repo)
        return {
            "status": "success",
            "trends": {
                "velocity": metrics.velocity_trend,
                "quality_score": metrics.quality_score,
                "avg_cycle_time_hours": metrics.avg_cycle_time_hours,
                "avg_review_latency_hours": metrics.avg_review_latency_hours,
                "total_prs": metrics.total_prs_merged,
                "total_issues": metrics.total_issues_closed,
                "unique_contributors": metrics.unique_contributors,
            },
        }
    except Exception as e:
        logger.error("get_performance_trends failed repo_url=%s: %s", repo_url, e)
        return {"status": "error", "error": str(e)}


AGENT_TOOLS = [
    {
        "name": "fetch_repository_stats",
        "description": "Fetch comprehensive statistics for a GitHub repository including PRs, issues, and reviews",
        "function": fetch_repository_stats,
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "GitHub repository URL"},
                "start_time": {"type": "string", "description": "Start time in ISO format (YYYY-MM-DDTHH:MM:SSZ)"},
                "end_time": {"type": "string", "description": "End time in ISO format (YYYY-MM-DDTHH:MM:SSZ)"},
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "analyze_metrics",
        "description": "Analyze repository metrics and answer specific questions about performance",
        "function": analyze_metrics,
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "GitHub repository URL"},
                "question": {"type": "string", "description": "Question to analyze about the metrics"},
                "start_time": {"type": "string", "description": "Start time in ISO format"},
                "end_time": {"type": "string", "description": "End time in ISO format"},
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "generate_summary",
        "description": "Generate a comprehensive summary of repository health with key findings and recommendations",
        "function": generate_summary,
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "GitHub repository URL"},
                "start_time": {"type": "string", "description": "Start time in ISO format"},
                "end_time": {"type": "string", "description": "End time in ISO format"},
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "get_top_contributors",
        "description": "Get the top contributors for a repository",
        "function": get_top_contributors,
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "GitHub repository URL"},
                "start_time": {"type": "string", "description": "Start time in ISO format"},
                "end_time": {"type": "string", "description": "End time in ISO format"},
                "limit": {"type": "integer", "description": "Number of top contributors to return", "default": 5},
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "get_performance_trends",
        "description": "Get performance trends and metrics for a repository",
        "function": get_performance_trends,
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "GitHub repository URL"},
                "start_time": {"type": "string", "description": "Start time in ISO format"},
                "end_time": {"type": "string", "description": "End time in ISO format"},
            },
            "required": ["repo_url"],
        },
    },
]
