from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import AnalysisSummary, EngineerMetrics, RepositoryMetrics
from main import app

client = TestClient(app)


def _make_metrics(**kwargs) -> RepositoryMetrics:
    defaults = dict(
        owner="microsoft",
        repo="vscode",
        analysis_period="2024-01-01 to 2024-01-31",
        total_prs_merged=1,
        total_issues_closed=1,
        total_reviews=1,
        avg_cycle_time_hours=24.0,
        median_cycle_time_hours=24.0,
        avg_review_latency_hours=2.0,
        median_review_latency_hours=2.0,
        unique_contributors=1,
        unique_reviewers=1,
        top_contributors=[EngineerMetrics(username="alice", prs_merged=1, reviews_completed=1, contribution_score=1.0)],
        top_reviewers=[EngineerMetrics(username="bob", prs_merged=0, reviews_completed=1, contribution_score=0.8)],
        velocity_trend="insufficient_data",
        quality_score=0.5,
    )
    defaults.update(kwargs)
    return RepositoryMetrics(**defaults)


def test_analyze_endpoint_returns_metrics(monkeypatch):
    import app.api.routes as routes_module

    dummy_github = Mock()
    dummy_analytics = Mock()
    dummy_llm = Mock()

    metrics = _make_metrics()
    analysis = AnalysisSummary(
        summary="Repository is healthy",
        key_findings=["Stable"],
        performance_insights={"cycle_time": "stable"},
        root_cause_hypotheses=["Consistent delivery"],
        recommendations=["Keep monitoring"],
        confidence_score=0.9,
    )

    dummy_github._parse_repo_url.return_value = ("microsoft", "vscode")
    dummy_github.fetch_repository_data.return_value = {"data": {"repository": {}}}
    dummy_analytics.generate_metrics.return_value = metrics
    dummy_llm.summarize_metrics_async = AsyncMock(return_value=analysis)

    monkeypatch.setattr(routes_module, "github_service", dummy_github)
    monkeypatch.setattr(routes_module, "analytics_service", dummy_analytics)
    monkeypatch.setattr(routes_module, "llm_service", dummy_llm)

    response = client.post("/api/analyze", json={"repo_url": "https://github.com/microsoft/vscode"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["metrics"]["owner"] == "microsoft"
    assert payload["analysis"]["summary"] == "Repository is healthy"


def test_analyze_endpoint_returns_bad_request(monkeypatch):
    import app.api.routes as routes_module

    dummy_github = Mock()
    dummy_github._parse_repo_url.side_effect = ValueError("bad url")

    monkeypatch.setattr(routes_module, "github_service", dummy_github)
    monkeypatch.setattr(routes_module, "analytics_service", Mock())
    monkeypatch.setattr(routes_module, "llm_service", Mock())

    response = client.post("/api/analyze", json={"repo_url": "bad-url"})

    assert response.status_code == 400
    assert response.json()["detail"] == "bad url"


def test_chat_endpoint_returns_response(monkeypatch):
    import app.api.routes as routes_module

    dummy_github = Mock()
    dummy_analytics = Mock()
    dummy_llm = Mock()

    metrics = _make_metrics(top_contributors=[], top_reviewers=[])

    dummy_github._parse_repo_url.return_value = ("microsoft", "vscode")
    dummy_github.fetch_repository_data.return_value = {"data": {"repository": {}}}
    dummy_analytics.generate_metrics.return_value = metrics
    dummy_llm.chat_message_async = AsyncMock(return_value="assistant response")

    monkeypatch.setattr(routes_module, "github_service", dummy_github)
    monkeypatch.setattr(routes_module, "analytics_service", dummy_analytics)
    monkeypatch.setattr(routes_module, "llm_service", dummy_llm)

    response = client.post(
        "/api/chat",
        json={
            "message": "Tell me about this repo",
            "repo_url": "https://github.com/microsoft/vscode",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "assistant response"
    assert payload["context"]["metrics"]["owner"] == "microsoft"


def test_chat_endpoint_no_repo_url(monkeypatch):
    import app.api.routes as routes_module

    dummy_llm = Mock()
    dummy_llm.chat_message_async = AsyncMock(return_value="generic response")

    monkeypatch.setattr(routes_module, "github_service", Mock())
    monkeypatch.setattr(routes_module, "analytics_service", Mock())
    monkeypatch.setattr(routes_module, "llm_service", dummy_llm)

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 200
    assert response.json()["message"] == "generic response"
    assert response.json()["context"] is None


def test_health_endpoint():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
