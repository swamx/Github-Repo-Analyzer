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

---

## IssuePilot — Multi-Agent Architecture

IssuePilot is a separate async pipeline that runs alongside the FastAPI server.
It introduces two new AI layers on top of the existing infrastructure:

### Component diagram

```text
Client
  │  POST /api/issue-pilot/fix  {repo_url, issue_numbers, create_pr}
  ▼
FastAPI  (issue_pilot/routes.py)
  │  HTTP 202 + job_id  ◄──────────────────────────────────────────┐
  │                                                                  │
  │  enqueue job_id → Redis Stream "issue_pilot:jobs"               │
  ▼                                                                  │
pipeline/worker.py  (blocking process, 1 per deployment)            │ poll
  │  XREADGROUP (consumer group "workers")                          │ /status/{job_id}
  ▼                                                                  │
coordinator/agent.py  ─── Google ADK LlmAgent  (Gemini 2.0 Flash)  │
  │                                                                  │
  │  ADK tool call sequence (strict order):                         │
  │  1. load_job()           ─ reads JobPayload from Redis Hash     │
  │  2. fetch_all_issues()   ─ GitHub REST API batch                │
  │  3. prepare_branches()   ─ creates N branches on remote         │
  │  4. dispatch_all_workers() ─ asyncio.gather (non-blocking fan-out)
  │  5. create_pull_requests() ─ GitHub API (if create_pr=True)     │
  │  6. mark_job_done()      ─ writes final state → Redis Hash ─────┘
  │
  │  dispatch_all_workers calls asyncio.gather over N coroutines:
  │
  ├── workers/claude_agent.py  (issue #42)
  ├── workers/claude_agent.py  (issue #57)   ← all run concurrently
  └── workers/claude_agent.py  (issue #91)

Each claude_agent.py coroutine:
  1. git clone --depth 1 <repo> into a temp dir
  2. Detect tech stack  → workers/skills.py
  3. Write lean-ctx 3.0 MCP config JSON  → workers/mcp_config.py
  4. Spawn subprocess:
       claude \
         --mcp-config /tmp/issue_pilot_mcp_<N>.json \
         --allowedTools "mcp__lean-ctx__ctx_*,Edit,Write,Bash" \
         --output-format json \
         -p "<issue prompt>"
  5. Wait (timeout=WORKER_TIMEOUT_S)
  6. Parse JSON result from stdout
  7. rm -rf tempdir, unlink mcp config
  8. Return WorkerResult → coordinator collects into IssuePlan list
```

### Inside each Claude Code worker

The `claude` subprocess receives a structured prompt that instructs it to use
lean-ctx 3.0 for all context operations:

```text
Prompt phases (in order):

Phase 1 — Context loading (lean-ctx 3.0, minimal tokens)
  ctx_overview()               → compressed project map (~200 tokens)
  ctx_knowledge(recall=skills) → load Python/FastAPI/TypeScript/… best practices
  ctx_search(pattern)          → find relevant code (compact ripgrep output)
  ctx_read(file, mode=map)     → file signatures only unless editing

Phase 2 — Root-cause analysis
  ctx_read(file, mode=full)    → only files that need editing
  Bash(git log --oneline -10)  → recent commit context

Phase 3 — Code fix
  Edit / Write                 → make minimal correct changes
  Bash(pytest -x -q)           → verify tests pass

Phase 4 — Commit + output
  Bash(git add -A && git commit -m "fix: #N …")
  Return JSON: {issue_number, title, plan, files_changed, tests_passed, commit_sha}
```

lean-ctx 3.0 compresses file reads by 60–99% vs raw cat/Read, allowing workers to
understand large codebases while spending far fewer tokens.

### Redis data model

```text
Redis Stream  "issue_pilot:jobs"
  Message fields: { job_id: "<uuid>" }
  Consumer group: "workers"

Redis Hash  "issue_pilot:job:<job_id>"   (TTL: JOB_TTL_SECONDS = 24h)
  Fields (JSON-serialised JobPayload):
    job_id, repo_url, issue_numbers, create_pr, base_branch
    status: queued | processing | done | failed
    issue_plans: [{issue_number, title, plan}, …]
    branch_name, pr_url, error
    created_at, updated_at
```

### Agent responsibilities

| Agent | Model | Role |
| --- | --- | --- |
| Coordinator | Gemini 2.0 Flash | Orchestration: load job, dispatch workers, collect results, create PRs |
| Worker (one per issue) | Claude Code (`claude` CLI) | Autonomous coding: research, implement fix, commit |

The coordinator uses a **fast, cheap** model (Gemini Flash) because its job is pure
orchestration — tool call sequencing, not reasoning. The heavy thinking happens inside
each Claude Code worker which has the full `claude-opus-4-8` brain.

### Error handling

| Scenario | Behaviour |
| --- | --- |
| Worker subprocess timeout | `asyncio.wait_for` kills the process; error stored in result |
| Worker exits non-zero | Stderr captured; job continues (other workers unaffected) |
| Branch already exists | Worker clones default branch and creates branch locally |
| PR creation fails | Logged; `branch_name` still set so branch is accessible |
| Coordinator tool call fails | ADK agent logs and proceeds to `mark_job_done` |
| Redis unavailable at enqueue | FastAPI returns 500; job never queued |

### Token efficiency via lean-ctx 3.0

Workers are instructed to follow CEP (Context Engineering Protocol) rules:

- `ctx_read(mode=map)` — signatures only; saves ~80% vs full file read
- `ctx_read(mode=signatures)` — function/class names only; ~95% savings
- `ctx_search` — compact ripgrep; saves ~60% vs raw `grep`
- `ctx_overview` — one-shot project map at session start; replaces repeated `ls`/`cat`
- `ctx_knowledge recall` — loads pre-compressed skill context; avoids re-reading docs

This means a worker can fully understand a 50-file Python service in ~2 000 tokens
instead of the ~40 000 tokens a naive approach would use.
