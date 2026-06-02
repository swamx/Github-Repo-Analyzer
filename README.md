# GitHub Engineering Intelligence API

AI-powered GitHub repository analytics with LLM-powered insights, conversational analysis,
and a built-in LLM-as-a-Judge prompt evaluation service.

---

## Overview

This application fetches GitHub repository data via the GraphQL API, computes engineering
metrics (cycle time, review latency, velocity, contributor scores), and generates AI-powered
summaries and recommendations through a **LiteLLM proxy** that routes to Claude Haiku
(primary) with automatic fallback to a local Ollama model.

### Capabilities

| Feature | Description |
| --- | --- |
| **Repository analysis** | PRs merged, issues closed, reviews, cycle time, review latency, velocity trend, quality score |
| **Engineer metrics** | Per-contributor: PRs, reviews, contribution score |
| **Time-range filtering** | Analyze any date range — last 30 days, a quarter, a specific sprint |
| **LLM insights** | AI-generated executive summary, key findings, root-cause hypotheses, recommendations |
| **Conversational chat** | Multi-turn Q&A with full metrics context injected into the system prompt |
| **LLM-as-a-Judge** | Blind prompt variant comparison — score 2–10 prompt strategies against the same input |
| **Resiliency** | Circuit breaker + retry + rate limiting on all LLM calls |
| **Caching** | Redis-backed analysis cache (10-min TTL) with in-memory fallback |
| **Observability** | Structured logging, OpenTelemetry traces/metrics, LiteLLM request tracking |

---

## Project Structure

```text
github-analyzer/
├── app/
│   ├── config.py                    # All settings from env vars
│   ├── api/
│   │   ├── routes.py                # /analyze, /chat, /metrics, /health
│   │   └── judge_routes.py          # /judge/compare
│   ├── models/
│   │   ├── schemas.py               # Core Pydantic models
│   │   └── judge_schemas.py         # Judge request/response models
│   └── services/
│       ├── llm_service.py           # LLM calls, prompt building, JSON extraction
│       ├── github_service.py        # GitHub GraphQL client
│       ├── analytics_service.py     # Pure metrics computation
│       ├── cache_service.py         # Redis + in-memory fallback
│       ├── circuit_breaker.py       # Sync circuit breaker (CLOSED/OPEN/HALF_OPEN)
│       ├── resilient_client.py      # Async: rate limit + retry + aiobreaker
│       ├── judge_service.py         # LLM-as-a-Judge orchestration
│       └── agent_tools.py           # Standalone callable tool functions
├── tests/                           # pytest test suite
├── main.py                          # App entry, OTEL setup, CORS, routers
├── litellm-config.yaml              # LiteLLM model aliases and fallback routing
├── requirements.txt                 # Pinned Python dependencies
├── Dockerfile
├── docker-compose.yml               # All services with health-checked startup
├── AUTHOR_NOTES.md                  # Submission notes: design decisions, trade-offs
├── ARCHITECTURE.md                  # System diagrams and service responsibilities
├── QUICKSTART.md                    # 5-minute setup guide
└── ROADMAP.md                       # Feature roadmap by phase
```

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- GitHub Personal Access Token (PAT) — scopes: `repo`, `read:user`
- Anthropic API key

### 1. Configure environment

```bash
cp .env.example .env
```

```env
# Required
GITHUB_TOKEN=your_github_pat
ANTHROPIC_API_KEY=your_anthropic_key
LITELLM_MASTER_KEY=your_chosen_secret   # any string — used as the proxy auth key
```

### 2. Start all services

```bash
docker-compose up --build
```

Startup order is health-checked: `postgres` → `litellm` → `api`. Ollama pulls the
llama3 model (~4 GB) on first start. Subsequent starts are instant.

### 3. Verify

```bash
curl http://localhost:8000/api/health
# {"status":"healthy","components":{"llm":{"status":"healthy",...}}}
```

### 4. Open the docs

- API Docs: <http://localhost:8000/docs>
- LiteLLM UI: <http://localhost:4000/ui>
- Open WebUI: <http://localhost:3001>

---

## API Endpoints

### POST `/api/analyze`

Fetch GitHub data, compute metrics, and generate an LLM analysis summary.

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/microsoft/vscode",
    "start_time": "2024-01-01T00:00:00Z",
    "end_time": "2024-03-31T23:59:59Z"
  }'
```

Response:

```json
{
  "status": "success",
  "metrics": {
    "owner": "microsoft",
    "repo": "vscode",
    "total_prs_merged": 312,
    "avg_cycle_time_hours": 18.4,
    "avg_review_latency_hours": 6.1,
    "quality_score": 0.84,
    "velocity_trend": "stable",
    "top_contributors": []
  },
  "analysis": {
    "summary": "Engineering health is strong with fast cycle times...",
    "key_findings": ["...", "..."],
    "performance_insights": { "cycle_time": "...", "review_process": "..." },
    "root_cause_hypotheses": ["..."],
    "recommendations": ["...", "...", "..."],
    "confidence_score": 0.82
  }
}
```

![Live /api/analyze response in Swagger UI showing LLM-generated insights](screenshots/Github-Sample-AnalyzerAPI.png)

### POST `/api/chat`

Conversational analysis. Optionally attach a `repo_url` to inject live metrics into
the system prompt — the model will cite specific numbers in its answers.

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Which reviewer is a bottleneck?",
    "repo_url": "https://github.com/microsoft/vscode",
    "conversation_history": []
  }'
```

Multi-turn: pass prior turns in `conversation_history` as
`[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`.
The last 10 turns are included.

### GET `/api/metrics`

Metrics only — no LLM call, faster.

```bash
GET /api/metrics?repo_url=https://github.com/microsoft/vscode&start_time=...&end_time=...
```

### GET `/api/health`

Live health check — circuit breaker state + LiteLLM ping.

```json
{
  "status": "healthy",
  "components": {
    "llm": {
      "status": "healthy",
      "model": "claude-haiku",
      "circuit_breaker": { "state": "closed", "failure_count": 0 },
      "litellm": { "status": "healthy" }
    }
  }
}
```

### POST `/api/judge/compare`

Blind LLM-as-a-Judge evaluation of 2–10 prompt variants against the same input.
The Swagger page (`/docs`) has a pre-filled example comparing a `concise-analyst`
vs `detailed-coach` system prompt.

```bash
curl -X POST http://localhost:8000/api/judge/compare \
  -H "Content-Type: application/json" \
  -d '{
    "input": "42 PRs merged, avg cycle time 68h, 8 contributors, 4 reviewers.",
    "variants": [
      {
        "name": "concise-analyst",
        "system_prompt": "Give a 3-bullet summary: good, needs attention, action item.",
        "user_prompt": "{input}",
        "model": "claude-haiku",
        "temperature": 0.2
      },
      {
        "name": "detailed-coach",
        "system_prompt": "Analyse metrics, identify bottlenecks, suggest two improvements.",
        "user_prompt": "Metrics:\n\n{input}\n\nWhat should we focus on?",
        "model": "claude-haiku",
        "temperature": 0.4
      }
    ],
    "judge_model": "claude-haiku",
    "criteria": ["accuracy", "helpfulness", "clarity", "conciseness"]
  }'
```

---

## LiteLLM — Why We Use It

All LLM requests go through a **LiteLLM proxy** instead of calling Anthropic or Ollama
directly. This gives us:

| Benefit | Detail |
| --- | --- |
| **Model aliasing** | The API code uses `claude-haiku` everywhere. Swapping the underlying model is a one-line change in `litellm-config.yaml` — no code change required. |
| **Automatic fallback** | If Claude fails (rate-limit, outage, quota), LiteLLM's `router_settings.fallbacks` transparently retries with `ollama-llama3`. The API never knows. |
| **Spend logging** | Every request is recorded in PostgreSQL. The LiteLLM UI at `http://localhost:4000/ui` shows cost, latency, and model breakdown. |
| **Request tracking** | Each call tags `user: "github-analyzer-api"` and `metadata.operation` (e.g. `summarize_metrics`, `judge_variant`) — visible in the UI for per-operation analysis. |
| **Key management** | The Anthropic API key lives only in the LiteLLM container. The API service holds only a `LITELLM_MASTER_KEY` (internal proxy key). |
| **`drop_params: true`** | Drops provider-unsupported params so the same payload works across Claude and Ollama. **Important**: do not send `response_format` to LiteLLM for Claude — see Troubleshooting. |

![LiteLLM request logs showing per-request token usage, cost, and github-analyzer-api user tag](screenshots/LiteLLM-TokenUsage.png)

Every row in the LiteLLM UI is tagged with `Team: github-analyzer-api` and the operation name, making it easy to see which endpoints are driving token spend.

### Model configuration (`litellm-config.yaml`)

```yaml
model_list:
  - model_name: claude-haiku      # alias the API uses
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: ollama-llama3
    litellm_params:
      model: ollama/llama3
      api_base: http://ollama:11434

router_settings:
  fallbacks:
    - claude-haiku:
        - ollama-llama3       # automatic fallback

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  drop_params: true           # silently drops unsupported params per provider
  set_verbose: true
```

---

## Resiliency

LLM calls go through three layers in the async path:

```text
Request
  ├─ AsyncLimiter   — 10 req/s cap (LLM_RATE_LIMIT / LLM_RATE_PERIOD)
  ├─ RetryPolicy    — 3 attempts, exponential backoff (0.2 s base, ±10% jitter)
  └─ CircuitBreaker — opens after 3 failed retry sequences (LITELLM_FAILURE_THRESHOLD)
                      probes after 60 s (LITELLM_FAILURE_RECOVERY_SECONDS)
```

The circuit breaker wraps the entire retry sequence, so one blip → one retry sequence,
not one failure count. Three complete retry sequences must fail before the circuit opens.

Circuit state is visible on `/api/health`.

---

## Metrics Reference

| Metric | Description |
| --- | --- |
| `avg_cycle_time_hours` | Average PR creation → merge time |
| `median_cycle_time_hours` | Median (less sensitive to outliers) |
| `avg_review_latency_hours` | Average time to first review |
| `median_review_latency_hours` | Median review latency |
| `velocity_trend` | `increasing` / `stable` / `decreasing` |
| `quality_score` | 0–1 score derived from cycle time distribution |
| `contribution_score` | Per-engineer weighted score (PRs + reviews) |
| `total_prs_merged` | PRs merged in the period |
| `total_issues_closed` | Issues closed in the period |
| `total_reviews` | Reviews submitted in the period |
| `unique_contributors` | Engineers who merged at least one PR |
| `unique_reviewers` | Engineers who submitted at least one review |

---

## Configuration

```env
# Required
GITHUB_TOKEN=                         # GitHub PAT (repo, read:user scopes)
ANTHROPIC_API_KEY=                    # Anthropic API key (used by LiteLLM only)
LITELLM_MASTER_KEY=                   # LiteLLM proxy auth key (any string)

# LLM routing
PRIMARY_MODEL=claude-haiku            # LiteLLM model alias
SECONDARY_MODEL=ollama-llama3         # Fallback alias (wired in litellm-config.yaml)
LLM_TEMPERATURE=0.2
LLM_RATE_LIMIT=10                     # Requests per LLM_RATE_PERIOD
LLM_RATE_PERIOD=1.0                   # Seconds

# LiteLLM proxy
LITELLM_API_BASE=http://litellm:4000  # Docker internal; localhost:4000 for local dev
LITELLM_API_KEY=                      # Auto-set from LITELLM_MASTER_KEY in docker-compose

# Circuit breaker
LITELLM_FAILURE_THRESHOLD=3           # Failed retry sequences before OPEN
LITELLM_FAILURE_RECOVERY_SECONDS=60   # Seconds before HALF_OPEN probe

# Cache
REDIS_HOST=redis
REDIS_PORT=6379
CACHE_TTL_SECONDS=600                 # 10 minutes

# Observability
OTEL_ENABLED=false                    # Set true to enable OpenTelemetry SDK
OTEL_EXPORTER_OTLP_ENDPOINT=          # Optional OTLP gRPC endpoint
```

---

## Service Ports

| Port | Service |
| --- | --- |
| 8000 | GitHub Analytics API + Swagger docs |
| 4000 | LiteLLM Proxy + UI (`/ui`) |
| 11434 | Ollama API |
| 3001 | Open WebUI (Ollama chat interface) |
| 6379 | Redis |
| 5432 | PostgreSQL (LiteLLM spend logs) |

---

## Running Tests

```bash
export GITHUB_TOKEN=test_token
pytest tests/ -v
```

---

## Troubleshooting

**`401 Unauthorized` from LiteLLM**
`LITELLM_API_KEY` must equal `LITELLM_MASTER_KEY`. `docker-compose.yml` wires them
automatically via `LITELLM_API_KEY: ${LITELLM_MASTER_KEY}`. If overriding, set both.

**`"Failed to parse LLM response as JSON"`**
Do not send `response_format: {"type": "json_object"}` for Claude via LiteLLM.
LiteLLM converts it to tool-calling which puts the response in `tool_calls`, not `content`.
JSON output is enforced via the system prompt instead. If you see this after a code change,
ensure the container is rebuilt: `docker-compose up --build api`.

**`open-webui` port conflict**
Open WebUI maps to host port 3001. If something else is on 3001,
change `"3001:8081"` in `docker-compose.yml`.

**Ollama model unavailable**
The `ollama-init` service pulls llama3 on first run. Check progress:
`docker logs ollama-init -f`

**LiteLLM container never healthy**
LiteLLM healthcheck uses Python urllib (curl/wget are absent from the Python slim image).
Do not replace it with a curl/wget command.

**GITHUB_TOKEN not set**
Add `GITHUB_TOKEN=your_pat` to `.env` before starting.
