import json
import types

import pytest

from app.models.schemas import EngineerMetrics, RepositoryMetrics


class DummyCacheService:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


@pytest.fixture
def metrics():
    return RepositoryMetrics(
        owner="microsoft",
        repo="vscode",
        analysis_period="2024-01-01 to 2024-12-31",
        total_prs_merged=100,
        total_issues_closed=45,
        total_reviews=200,
        avg_cycle_time_hours=12.2,
        median_cycle_time_hours=10.5,
        avg_review_latency_hours=4.6,
        median_review_latency_hours=3.8,
        unique_contributors=24,
        unique_reviewers=15,
        top_contributors=[EngineerMetrics(username="alice", prs_merged=10, reviews_completed=20, contribution_score=0.9)],
        top_reviewers=[EngineerMetrics(username="bob", prs_merged=9, reviews_completed=18, contribution_score=0.8)],
        velocity_trend="increasing",
        quality_score=0.88,
    )


def _make_http_response(content: str):
    """Return a fake httpx response object with the given chat completion content."""
    return types.SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": content}}]},
    )


def test_chat_message_uses_primary_model(monkeypatch):
    monkeypatch.setattr("app.services.llm_service.CacheService", DummyCacheService)

    from app.services.llm_service import LLMService

    llm = LLMService()

    monkeypatch.setattr(
        "app.services.llm_service.httpx",
        types.SimpleNamespace(post=lambda *a, **kw: _make_http_response("Primary response")),
    )

    response = llm.chat_message("hello")

    assert response == "Primary response"
    assert llm.last_backend_used == "primary"


def test_chat_message_raises_when_primary_fails(monkeypatch):
    monkeypatch.setattr("app.services.llm_service.CacheService", DummyCacheService)

    from app.services.llm_service import LLMService

    llm = LLMService()

    def _fail(*args, **kwargs):
        raise RuntimeError("LiteLLM down")

    monkeypatch.setattr(
        "app.services.llm_service.httpx",
        types.SimpleNamespace(post=_fail),
    )

    with pytest.raises(RuntimeError, match="Chat message failed"):
        llm.chat_message("hello")


def test_input_guardrail_rejects_blank_message(monkeypatch):
    monkeypatch.setattr("app.services.llm_service.CacheService", DummyCacheService)

    from app.services.llm_service import LLMService

    llm = LLMService()

    with pytest.raises(ValueError, match="empty"):
        llm.input_guardrail("   ")


def test_summarize_metrics_returns_parsed_json(monkeypatch, metrics):
    monkeypatch.setattr("app.services.llm_service.CacheService", DummyCacheService)

    from app.services.llm_service import LLMService

    llm = LLMService()

    payload = json.dumps({
        "summary": "Repository is healthy",
        "key_findings": ["More reviews"],
        "performance_insights": {"cycle_time": "improving"},
        "root_cause_hypotheses": ["Team efficiency"],
        "recommendations": ["Continue monitoring"],
        "confidence_score": 0.91,
    })
    monkeypatch.setattr(
        "app.services.llm_service.httpx",
        types.SimpleNamespace(post=lambda *a, **kw: _make_http_response(payload)),
    )

    summary = llm.summarize_metrics(metrics)

    assert summary.summary == "Repository is healthy"
    assert summary.confidence_score == pytest.approx(0.91)
