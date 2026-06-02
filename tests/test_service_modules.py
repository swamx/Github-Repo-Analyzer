import types
import pytest

from app.models.schemas import AnalysisSummary, EngineerMetrics, RepositoryMetrics
from app.services.analytics_service import AnalyticsService
from app.services.cache_service import CacheService
from app.services.github_service import GitHubService
import app.services.agent_tools as agent_tools


class DummyRedis:
    def __init__(self, store=None, fail_get=False, fail_set=False):
        self.store = store or {}
        self.fail_get = fail_get
        self.fail_set = fail_set

    def get(self, key):
        if self.fail_get:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, payload):
        if self.fail_set:
            raise RuntimeError("redis down")
        self.store[key] = payload


class DummyResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class DummyCache:
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class DummyGithubService:
    def __init__(self, data=None):
        self.data = data or {}

    def _parse_repo_url(self, repo_url):
        if repo_url.startswith("http"):
            parts = [p for p in repo_url.strip("/").split("/") if p]
            return parts[-2], parts[-1]
        return repo_url.split("/")

    def fetch_repository_data(self, owner, repo, start_time=None, end_time=None):
        return self.data


@pytest.fixture
def sample_metrics():
    return RepositoryMetrics(
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
        top_contributors=[
            EngineerMetrics(
                username="alice",
                prs_merged=1,
                reviews_completed=1,
                contribution_score=1.0,
            )
        ],
        top_reviewers=[
            EngineerMetrics(
                username="bob",
                prs_merged=0,
                reviews_completed=1,
                contribution_score=0.8,
            )
        ],
        velocity_trend="insufficient_data",
        quality_score=0.5,
    )


@pytest.fixture
def sample_summary():
    return AnalysisSummary(
        summary="Repository is healthy",
        key_findings=["High-quality review process"],
        performance_insights={"cycle_time": "stable"},
        root_cause_hypotheses=["Consistent delivery"],
        recommendations=["Keep monitoring"],
        confidence_score=0.9,
    )


def test_cache_service_returns_memory_fallback(monkeypatch):
    import app.services.cache_service as cache_module

    monkeypatch.setattr(cache_module.redis, "Redis", lambda *args, **kwargs: DummyRedis(fail_get=True, fail_set=True))

    service = CacheService()
    service.set("fallback-key", {"value": 123})

    assert service.get("fallback-key") == {"value": 123}


def test_cache_service_reads_from_redis(monkeypatch):
    import app.services.cache_service as cache_module

    store = {}

    def fake_redis(*args, **kwargs):
        return DummyRedis(store=store)

    monkeypatch.setattr(cache_module.redis, "Redis", fake_redis)

    service = CacheService()
    service.set("cache-key", {"value": 456})

    assert service.get("cache-key") == {"value": 456}


def test_github_service_fetches_and_caches(monkeypatch):
    import app.services.github_service as github_module

    payload = {
        "data": {
            "repository": {
                "pullRequests": {"nodes": []},
                "issues": {"nodes": []}
            }
        }
    }

    def fake_post(*args, **kwargs):
        return DummyResponse(payload)

    monkeypatch.setattr(github_module.requests, "post", fake_post)

    service = GitHubService()
    service.cache = DummyCache()

    data = service.fetch_repository_data("owner", "repo")
    assert data == payload
    assert service.cache.get("github:owner:repo:snapshot:all_all") == payload


def test_github_service_uses_cache_without_request(monkeypatch):
    import app.services.github_service as github_module

    payload = {
        "data": {
            "repository": {
                "pullRequests": {"nodes": []},
                "issues": {"nodes": []}
            }
        }
    }

    def fake_post(*args, **kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(github_module.requests, "post", fake_post)

    service = GitHubService()
    service.cache = DummyCache({"github:owner:repo:snapshot:all_all": payload})

    data = service.fetch_repository_data("owner", "repo")
    assert data == payload


def test_github_service_raises_runtime_error_on_failure(monkeypatch):
    import app.services.github_service as github_module

    def fake_post(*args, **kwargs):
        raise github_module.requests.exceptions.RequestException("network down")

    monkeypatch.setattr(github_module.requests, "post", fake_post)

    service = GitHubService()
    service.cache = DummyCache()

    with pytest.raises(RuntimeError, match="Failed to fetch GitHub data"):
        service.fetch_repository_data("owner", "repo")


def test_github_service_commit_history(monkeypatch):
    import app.services.github_service as github_module

    payload = {"data": {"repository": {"defaultBranchRef": {"target": {"history": {"nodes": []}}}}}}

    monkeypatch.setattr(github_module.requests, "post", lambda *args, **kwargs: DummyResponse(payload))

    service = GitHubService()

    response = service.get_commit_history("owner", "repo")
    assert response == payload


def test_analytics_service_generate_metrics():
    service = AnalyticsService()

    github_data = {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "author": {"login": "alice"},
                            "createdAt": "2024-01-01T00:00:00Z",
                            "mergedAt": "2024-01-02T00:00:00Z",
                            "reviews": {
                                "nodes": [
                                    {
                                        "createdAt": "2024-01-01T02:00:00Z",
                                        "author": {"login": "bob"},
                                    }
                                ]
                            },
                        }
                    ]
                },
                "issues": {
                    "nodes": [
                        {
                            "author": {"login": "alice"},
                            "createdAt": "2024-01-03T00:00:00Z",
                            "closedAt": "2024-01-03T01:00:00Z",
                        }
                    ]
                },
            }
        }
    }

    metrics = service.generate_metrics(github_data, "microsoft", "vscode")

    assert metrics.total_prs_merged == 1
    assert metrics.total_issues_closed == 1
    assert metrics.total_reviews == 1
    assert metrics.avg_cycle_time_hours == pytest.approx(24.0)
    assert metrics.avg_review_latency_hours == pytest.approx(2.0)
    assert metrics.unique_contributors == 1
    assert metrics.quality_score == pytest.approx(0.5)
    assert metrics.top_contributors[0].username == "alice"
    assert metrics.velocity_trend == "insufficient_data"


def test_analytics_service_helpers():
    service = AnalyticsService()

    assert service._in_time_range(__import__("datetime").datetime(2024, 1, 2), __import__("datetime").datetime(2024, 1, 1), __import__("datetime").datetime(2024, 1, 3))
    assert not service._in_time_range(__import__("datetime").datetime(2024, 1, 10), __import__("datetime").datetime(2024, 1, 1), __import__("datetime").datetime(2024, 1, 3))
    assert service._calculate_velocity_trend([10, 20]) == "decreasing"
    assert service._calculate_velocity_trend([20, 10]) == "increasing"
    assert service._calculate_quality_score(24, 24) == pytest.approx(0.5)




def test_agent_tools_success_paths(monkeypatch, sample_metrics, sample_summary):
    dummy_github = DummyGithubService({"data": {"repository": {}}})
    dummy_analytics = type("DummyAnalytics", (), {})()
    dummy_llm = type("DummyLLM", (), {})()

    dummy_analytics.generate_metrics = lambda github_data, owner, repo, start_time=None, end_time=None: sample_metrics
    dummy_llm.analyze_metrics = lambda metrics, question=None: "Repo looks stable"
    dummy_llm.summarize_metrics = lambda metrics: sample_summary

    monkeypatch.setattr(agent_tools, "_github_service", dummy_github)
    monkeypatch.setattr(agent_tools, "_analytics_service", dummy_analytics)
    monkeypatch.setattr(agent_tools, "_llm_service", dummy_llm)

    stats = agent_tools.fetch_repository_stats("https://github.com/microsoft/vscode")
    assert stats["status"] == "success"
    assert stats["metrics"]["owner"] == "microsoft"

    analysis = agent_tools.analyze_metrics("https://github.com/microsoft/vscode", question="any issues?")
    assert analysis["status"] == "success"
    assert analysis["analysis"] == "Repo looks stable"

    summary = agent_tools.generate_summary("https://github.com/microsoft/vscode")
    assert summary["status"] == "success"
    assert summary["summary"]["summary"] == "Repository is healthy"

    contributors = agent_tools.get_top_contributors("https://github.com/microsoft/vscode")
    assert contributors["status"] == "success"
    assert contributors["count"] == 1

    trends = agent_tools.get_performance_trends("https://github.com/microsoft/vscode")
    assert trends["status"] == "success"
    assert trends["trends"]["quality_score"] == pytest.approx(sample_metrics.quality_score)


def test_agent_tools_error_path(monkeypatch):
    dummy_github = type("DummyGithub", (), {})()
    dummy_github._parse_repo_url = lambda repo_url: (_ for _ in ()).throw(ValueError("bad repo url"))

    monkeypatch.setattr(agent_tools, "_github_service", dummy_github)
    monkeypatch.setattr(agent_tools, "_analytics_service", type("DummyAnalytics", (), {})())
    monkeypatch.setattr(agent_tools, "_llm_service", type("DummyLLM", (), {})())

    result = agent_tools.fetch_repository_stats("not-a-repo")
    assert result["status"] == "error"
    assert "bad repo url" in result["error"]
