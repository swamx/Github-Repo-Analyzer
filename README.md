# GitHub Engineering Intelligence API

AI-powered GitHub analytics, LLM-as-a-Judge evaluation, autonomous issue fixing, and **AI Code Review**.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- GitHub Personal Access Token (PAT)
- Anthropic API key

### 1. Configure
```bash
cp .env.example .env
# Edit .env: add GITHUB_TOKEN, ANTHROPIC_API_KEY, LITELLM_MASTER_KEY
```

### 2. Start
```bash
docker-compose up --build
```

### 3. Verify
```bash
curl http://localhost:8000/api/health
```

### 4. Explore
- **API Docs**: http://localhost:8000/docs
- **LiteLLM UI**: http://localhost:4000/ui

---

## Features

| Feature | Docs |
|---------|------|
| **Repository Analysis** | Metrics: cycle time, review latency, velocity |
| **LLM Insights** | AI summaries, recommendations, root-cause analysis |
| **Chat** | Conversational Q&A with metrics context |
| **LLM-as-a-Judge** | Blind prompt comparison & scoring |
| **Code Review** | [AI PR review](docs/CODE_REVIEW.md) — correctness, security, testing |
| **IssuePilot** | Autonomous issue fixing (multi-agent pipeline) |

---

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/analyze` | Fetch repo data, compute metrics, generate LLM analysis |
| POST | `/api/chat` | Conversational analysis with metrics context |
| GET | `/api/metrics` | Metrics only (no LLM) |
| POST | `/api/judge/compare` | Blind LLM-as-a-Judge prompt evaluation |
| POST | `/api/code-review/review` | Trigger PR review |
| POST | `/api/code-review/webhook` | GitHub webhook (async) |
| POST | `/api/code-review/feedback` | Record developer feedback |
| GET | `/api/code-review/stats` | Review metrics & accuracy |
| POST | `/api/issue-pilot/fix` | Queue autonomous issue fixing |
| GET | `/api/issue-pilot/status/{id}` | Check fix pipeline status |

---

## Documentation

All docs are in [`docs/`](docs/):

- **[INDEX.md](docs/INDEX.md)** — Documentation guide
- **[QUICKSTART.md](docs/QUICKSTART.md)** — 5-minute setup
- **[CODE_REVIEW.md](docs/CODE_REVIEW.md)** — PR review system
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** — System design
- **[FEEDBACK_ARCHITECTURE.md](docs/FEEDBACK_ARCHITECTURE.md)** — Design reference

---

## Code Review (New)

AI-powered PR review with multi-agent analysis:

```bash
# Trigger a review
curl -X POST http://localhost:8000/api/code-review/review \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/repo", "pr_number": 42, "dry_run": true}'
```

Features:
- Parallel agents: correctness, security, testing
- GitHub webhook support
- Developer feedback collection
- Metrics & evaluation

See [CODE_REVIEW.md](docs/CODE_REVIEW.md) for setup & usage.

---

## Configuration

### Required Environment Variables
```bash
GITHUB_TOKEN=ghp_xxxxx           # GitHub PAT
ANTHROPIC_API_KEY=sk-ant-xxxxx   # Anthropic
LITELLM_MASTER_KEY=xxxxx         # LiteLLM proxy key
```

### Optional (Code Review)
```bash
GITHUB_WEBHOOK_SECRET=xxxxx      # For webhooks
REVIEW_CONFIDENCE_INFO_THRESHOLD=0.65
REVIEW_CONFIDENCE_BLOCK_THRESHOLD=0.85
```

See `.env.example` for all options.

---

## Architecture

```
┌─ Repository Analysis
├─ LLM Insights  
├─ Chat (conversational)
├─ LLM-as-a-Judge (prompt evaluation)
├─ Code Review (PR analysis) [NEW]
└─ IssuePilot (autonomous fixing)
  └─ Google ADK (orchestrator) + Claude Code (workers)
```

Data flows through **LiteLLM proxy** (model aliasing, fallback routing, spend tracking) → **Circuit breaker** (resilience) → **Redis cache** (10-min TTL).

---

## Project Structure

```
github-analyzer/
├── app/services/code_review/     (Code review pipeline — NEW)
│   ├── schemas.py, github_client.py, diff_analyzer.py
│   ├── orchestrator.py, agents.py, validation.py
│   ├── dedup.py, scoring.py, comment_formatter.py
│   ├── feedback_store.py, service.py
│   └── __init__.py
├── app/api/                       (API routes)
│   ├── code_review_routes.py      (NEW)
│   ├── routes.py, judge_routes.py
│   └── __init__.py
├── app/services/                  (Core services)
│   ├── llm_service.py, github_service.py, analytics_service.py
│   ├── cache_service.py, circuit_breaker.py, resilient_client.py
│   └── ...
├── issue_pilot/                   (IssuePilot: issue fixing)
├── tests/                         (Test suite)
├── docs/                          (Documentation)
├── main.py                        (App entry, routers, OTEL)
├── litellm-config.yaml            (LLM routing, fallbacks)
├── requirements.txt
├── Dockerfile, docker-compose.yml
└── README.md (this file)
```

---

## Ports

| Port | Service |
|------|---------|
| 8000 | API + Swagger docs |
| 4000 | LiteLLM UI |
| 11434 | Ollama API |
| 3001 | Open WebUI |
| 6379 | Redis |
| 5432 | PostgreSQL |

---

## Troubleshooting

See [QUICKSTART.md](docs/QUICKSTART.md#troubleshooting) for common issues.

---

## Development

```bash
# Run without Docker (local dev)
python main.py

# Run tests
pytest tests/test_code_review.py -v

# View Swagger docs
http://localhost:8000/docs
```

---

## License & Attribution

See [docs/AUTHOR_NOTES.md](docs/AUTHOR_NOTES.md) for design decisions and acknowledgments.

---

**Ready to start?** → See [docs/QUICKSTART.md](docs/QUICKSTART.md)
