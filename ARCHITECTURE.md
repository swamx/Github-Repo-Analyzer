# Architecture

## System Diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Client / Browser                          │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/JSON
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI  (port 8000)                           │
│                                                                   │
│  POST /api/analyze          GET  /api/metrics                    │
│  POST /api/chat             GET  /api/health                     │
│  POST /api/judge/compare                                         │
└──────────┬─────────────────────────────────────────┬────────────┘
           │                                         │
    ┌──────┴────────┐                    ┌───────────┴────────────┐
    ▼               ▼                    ▼                         │
┌──────────┐  ┌──────────────┐  ┌──────────────────┐             │
│  GitHub  │  │  Analytics   │  │   Judge Service  │             │
│  Service │─▶│  Service     │  │  (blind variant  │             │
│ GraphQL  │  │  (pure       │  │   comparison)    │             │
│  + cache │  │  computation)│  └────────┬─────────┘             │
└──────────┘  └──────┬───────┘           │                        │
                     │                   │                        │
                     └──────────────┬────┘                        │
                                    ▼                             │
                         ┌──────────────────┐                    │
                         │   LLM Service    │◄───────────────────┘
                         │  ┌────────────┐  │
                         │  │Circuit     │  │
                         │  │Breaker     │  │
                         │  ├────────────┤  │
                         │  │Resilient   │  │
                         │  │Client      │  │
                         │  │(rate+retry)│  │
                         │  └────────────┘  │
                         └────────┬─────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │   LiteLLM Proxy      │
                       │   (port 4000)        │
                       │                      │
                       │  Primary:            │
                       │   claude-haiku       │
                       │  Fallback:           │
                       │   ollama-llama3      │
                       └──────┬───────────────┘
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
    ┌──────────────────┐          ┌──────────────────┐
    │  Anthropic API   │          │  Ollama :11434   │
    │  Claude Haiku    │          │  Llama 3         │
    └──────────────────┘          └──────────────────┘

Supporting services (Docker Compose):
  Redis    :6379  — analysis cache + GitHub response cache
  Postgres :5432  — LiteLLM spend/audit log
  Open WebUI :3001 — Ollama chat UI (optional)
```

---

## LLM Routing

The application never calls Anthropic or Ollama directly. All requests go through the
**LiteLLM proxy** at `http://litellm:4000`.

### Why LiteLLM?

1. **Model aliasing** — the code always sends `model: claude-haiku`. The real model ID
   (`anthropic/claude-haiku-4-5-20251001`) is in `litellm-config.yaml`. Swapping models
   requires no code change.

2. **Automatic fallback** — `router_settings.fallbacks` retries `ollama-llama3` if Claude
   fails. Transparent to the application layer.

3. **Spend tracking** — every request is logged to PostgreSQL and visible in the LiteLLM
   UI (`/ui`). All calls include `user: "github-analyzer-api"` and `metadata.operation`
   for per-feature cost breakdown.

4. **Key isolation** — `ANTHROPIC_API_KEY` never leaves the LiteLLM container. The API
   service only holds `LITELLM_API_KEY` (the proxy master key).

5. **`drop_params: true`** — silently drops params unsupported by a provider. Needed
   because Claude and Ollama accept different parameter sets.

### Critical: `response_format` + Claude = empty content

Do **not** send `response_format: {"type": "json_object"}` to LiteLLM for Claude models.
LiteLLM converts this to Claude's internal tool-calling mechanism, which puts the JSON
in `choices[0].message.tool_calls[0].function.arguments` and sets `content = null`.

**Fix**: JSON is enforced via system prompt instructions only:
> "your entire response must be a single valid JSON object. Start with { and end with }."

`_extract_response_content` also has a fallback that extracts from `tool_calls` if
content is null, so the service degrades gracefully even if this re-appears.

---

## Service Responsibilities

### `llm_service.py`

- Builds prompts: `_build_summary_prompt` (JSON schema with real benchmark context),
  `_build_chat_system_prompt` (full metrics table for conversational chat)
- Executes calls via `_execute_chat_async` → `ResilientClient` → LiteLLM proxy
- Extracts content: `_extract_response_content` handles OpenAI choices, Anthropic
  content arrays, and `tool_calls` fallback
- Strips JSON fences: `_strip_json_fences` removes `` ```json ``` `` and extracts
  outermost `{...}` from prose
- Caches summaries in Redis keyed by `summary:{owner}:{repo}:{period}`

### `github_service.py`

- GraphQL queries for PRs (merged), issues (closed), reviews, commits
- Caches raw GitHub data in Redis keyed by `github:{owner}:{repo}:snapshot:{time_range}`
- Raises `RuntimeError` on HTTP failure; `ValueError` on GraphQL errors
- Null-safe author handling: GitHub bots and deleted accounts return `author: null`

### `analytics_service.py`

- Pure computation — no I/O
- Computes: cycle time (creation → merge), review latency (creation → first review),
  per-engineer contribution scores, velocity trend (comparing early vs late half of period),
  quality score (normalized cycle time)
- Handles `null` author fields: `(pr.get("author") or {}).get("login", "unknown")`

### `cache_service.py`

- Redis primary with in-memory dict fallback
- On Redis failure: logs warning, stores in memory (survives Redis outage transparently)
- TTL: `CACHE_TTL_SECONDS` (default 600s)

### `circuit_breaker.py` (sync)

- Custom implementation, used by the sync `_execute_chat` path
- States: CLOSED → OPEN → HALF_OPEN → CLOSED
- CLOSED → OPEN: after `failure_threshold` consecutive failures
- OPEN → HALF_OPEN: after `recovery_timeout` seconds
- HALF_OPEN → CLOSED: after 2 consecutive successes
- HALF_OPEN → OPEN: on any failure during probe

### `resilient_client.py` (async)

- Used by all API route paths
- Layers: `AsyncLimiter` (rate) → `RetryPolicy` (exponential backoff) → `aiobreaker`
- Retry is **inner**, circuit breaker is **outer**: all retries must fail before the
  breaker counts one failure. Three complete retry sequences = circuit opens.
- OpenTelemetry span per call, counters for requests/failures

### `judge_service.py`

- `compare()`: fan-out all variant runs via `asyncio.gather(return_exceptions=True)`
- `_aggregate_runs()`: maps runs to variants by `i // runs_per_variant` (not modulo)
- `_judge()`: sends all outputs blind (labelled A/B/C) to the judge model at temp=0.1
- `_build_response()`: resolves labels → names, computes total_score fallback,
  builds `JudgeResponse` with per-variant scores and pairwise comparisons

### `agent_tools.py`

- Standalone functions callable by external agents or directly
- Each returns `{"status": "success", ...}` or `{"status": "error", "error": "..."}` — never raises

---

## Data Models

```text
AnalyzeRequest  →  AnalyzeResponse
                     ├── RepositoryMetrics
                     │     └── EngineerMetrics[]
                     └── AnalysisSummary
                           ├── summary (str)
                           ├── key_findings (list)
                           ├── performance_insights (dict)
                           ├── root_cause_hypotheses (list)
                           ├── recommendations (list)
                           └── confidence_score (float)

ChatRequest     →  ChatResponse

JudgeRequest    →  JudgeResponse
                     ├── winner (str)
                     ├── variants[] → VariantResult
                     │     ├── criterion_scores: {criterion: CriterionScore}
                     │     └── total_score (float)
                     ├── judge_reasoning (str)
                     └── pairwise_comparisons[] → PairwiseComparison
```

---

## Cache Keys

| Key pattern | Contents | TTL |
| --- | --- | --- |
| `github:{owner}:{repo}:snapshot:{time}` | Raw GraphQL response | `CACHE_TTL_SECONDS` |
| `summary:{owner}:{repo}:{period}` | LLM-generated `AnalysisSummary` | `CACHE_TTL_SECONDS` |

---

## Error Handling

| Source | Exception | HTTP status |
| --- | --- | --- |
| Bad repo URL or empty input | `ValueError` | 400 |
| LLM returns empty content | `ValueError` (with raw LLM text as detail) | 400 |
| GitHub HTTP failure | `RuntimeError` | 500 |
| All LLM retries exhausted | `RuntimeError` | 500 |
| Circuit breaker open | `CircuitBreakerError` → `RuntimeError` | 500 |
| Unexpected | `Exception` | 500 |

All errors are logged before being surfaced as `HTTPException`.

---

## Observability

### Structured logging

`logging.basicConfig(level=INFO)` in `main.py`. Every module uses
`logger = logging.getLogger(__name__)`. Log lines include the module path,
e.g. `app.services.llm_service summarize_metrics_async succeeded backend=primary content_len=1243`.

### OpenTelemetry (optional)

Enabled by `OTEL_ENABLED=true` or `OTEL_EXPORTER_OTLP_ENDPOINT`. Configured in
`main.py → _configure_otel()`:

- **Traces**: `TracerProvider` + `BatchSpanProcessor`; console or OTLP gRPC
- **Metrics**: `MeterProvider` + `PeriodicExportingMetricReader` (60s interval)
  - `resilient_client_requests` counter
  - `resilient_client_failures` counter (with `reason` attribute)

### LiteLLM request tracking

Every request includes:

```python
"user": "github-analyzer-api",
"metadata": {
    "source": "github-analyzer-api",
    "service_version": "2.0.0",
    "operation": "summarize_metrics"  # or chat / judge_variant / judge_eval
}
```

Visible in the LiteLLM UI at `http://localhost:4000/ui`.

![LiteLLM request logs showing token usage, cost, model, and github-analyzer-api tagging](screenshots/LiteLLM-TokenUsage.png)

---

## Security

- GitHub token and Anthropic API key are in environment variables only — never logged
- `LITELLM_API_KEY` = `LITELLM_MASTER_KEY`: wired automatically in `docker-compose.yml`
- CORS: `allow_origins=["*"]`, `allow_credentials=False` (credentials=True is invalid with wildcard origin)
- Redis unauthenticated by default — add AUTH in production
- Input validated by Pydantic; `input_guardrail()` rejects empty messages

---

## Docker Compose Startup Order

```text
postgres (healthcheck: pg_isready)
    └─▶ litellm (healthcheck: python urllib on /)
            └─▶ api (depends on redis + litellm both healthy)

ollama (healthcheck: ollama list)
    └─▶ ollama-init (pulls llama3 model, exits)

redis (healthcheck: redis-cli ping)
    └─▶ api (above)
```

The `api` container will not start until both `redis` and `litellm` report healthy.
This prevents the `ConnectError` that occurs when the API tries to call LiteLLM before
it finishes initializing.
