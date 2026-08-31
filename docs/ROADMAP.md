# Development Roadmap

Last updated: 2026-06-03

---

## Phase 1: Shipped ✅

### Core Platform

- [x] Package structure (`app/api`, `app/models`, `app/services`)
- [x] Time-range support for GitHub queries (any start/end date)
- [x] Comprehensive Pydantic data models with validation
- [x] GitHub GraphQL API client with caching

### REST API

- [x] `POST /api/analyze` — metrics + LLM analysis summary
- [x] `POST /api/chat` — multi-turn conversational analytics
- [x] `GET /api/metrics` — metrics-only (no LLM)
- [x] `GET /api/health` — circuit breaker state + live LiteLLM ping

### LLM Infrastructure

- [x] LiteLLM proxy integration (model aliasing, automatic fallback)
- [x] Claude Haiku as primary model via Anthropic API
- [x] Ollama / llama3 as fallback model (local, no API cost)
- [x] Configurable `PRIMARY_MODEL` / `SECONDARY_MODEL` via env vars
- [x] LiteLLM request tracking (`user`, `metadata.operation` per call)

### Resiliency

- [x] Custom sync circuit breaker (CLOSED → OPEN → HALF_OPEN)
- [x] Async circuit breaker via `aiobreaker`
- [x] Retry policy with exponential backoff and jitter (3 attempts)
- [x] Async rate limiter (10 req/s default)
- [x] Redis cache with in-memory fallback

### Eval Harness

- [x] `POST /api/judge/compare` — LLM-as-a-Judge service
- [x] Blind labelling (A/B/C) to prevent label bias in scoring
- [x] Concurrent variant execution via `asyncio.gather`
- [x] Multi-run averaging per variant
- [x] Pairwise comparison output
- [x] Pre-filled Swagger example with `concise-analyst` vs `detailed-coach`

### Observability

- [x] Structured logging with module-path prefixes
- [x] OpenTelemetry SDK (traces + metrics, opt-in via `OTEL_ENABLED`)
- [x] OTEL counters: `resilient_client_requests`, `resilient_client_failures`

### Deployment

- [x] Dockerfile (Python 3.11 slim)
- [x] Docker Compose with all services: api, litellm, redis, postgres, ollama, open-webui
- [x] Health-checked startup ordering (postgres → litellm → api)
- [x] Pinned `requirements.txt`
- [x] `.env.example` template

### Documentation

- [x] `README.md` — full API reference and LiteLLM rationale
- [x] `QUICKSTART.md` — 5-minute setup
- [x] `ARCHITECTURE.md` — system diagrams and service responsibilities
- [x] `AUTHOR_NOTES.md` — submission notes: design decisions, trade-offs, gotchas
- [x] `ROADMAP.md` — this file

---

## Phase 2: IssuePilot — Autonomous Issue Fixer (In Progress 🔄)

### Architecture

- [x] `issue_pilot/` module — self-contained pipeline package
- [x] `POST /api/issue-pilot/fix` — async job submission (HTTP 202 + job_id)
- [x] `GET /api/issue-pilot/status/{job_id}` — polling endpoint
- [x] Redis Streams queue — `issue_pilot:jobs` stream + consumer group
- [x] Google ADK coordinator agent (Gemini 2.0 Flash) — orchestrates full job lifecycle
- [x] Parallel Claude Code workers — one `claude` CLI subprocess per issue
- [x] lean-ctx 3.0 MCP integration — 60–99% token reduction in workers
- [x] Tech-stack skill detection — auto-loads Python/TS/Go/… best practices via ctx_knowledge
- [x] GitHub branch creation per issue
- [x] Optional PR creation with AI-generated description per issue

### Pending

- [ ] Task status API — expose pipeline stage (queued → planning → coding → PR opened) via Postgres or Redis; list all tasks for a given repo
- [ ] LiteLLM routing for Claude Code worker calls — route via proxy with `repo_url` + `issue_number` metadata flags; requires downside review first
- [ ] Webhook: POST to caller-supplied URL when a job reaches `done` or `failed`
- [ ] Web UI — live status page per job (SSE-driven progress stream)
- [ ] Worker concurrency cap — honour `MAX_PARALLEL_WORKERS` in asyncio.gather
- [ ] Retry logic — re-queue failed workers up to N times before marking issue failed

---

## Phase 3: Core Analytics Enhancements

### A. Advanced Metrics

- [ ] Lead time for changes (commit → production)
- [ ] Deployment frequency
- [ ] Mean time to recovery (MTTR)
- [ ] Change failure rate
- [ ] Time-to-first-response SLA tracking
- [ ] Code review effectiveness scoring (comments-to-approval ratio)

### B. Historical Data & Trending

- [ ] Persist metrics to TimescaleDB or PostgreSQL time-series table
- [ ] Trend charts and visualizations (replace on-demand GraphQL with DB queries)
- [ ] Anomaly detection in velocity
- [ ] Quarter-over-quarter comparison endpoint

### C. Enhanced Chat

- [ ] Multi-repository analysis in a single conversation
- [ ] Cross-team comparisons
- [ ] Streaming responses (Server-Sent Events)

### D. Testing & Quality

- [ ] Integration tests hitting real LiteLLM (docker-compose test profile)
- [ ] Load testing (k6)
- [ ] Code coverage tracking (target: 80%+)
- [ ] Judge eval harness extended with golden-set regression tests

---

## Phase 3: Enterprise Features

### A. Authentication & Authorization

- [ ] API key validation middleware
- [ ] OAuth 2.0 / GitHub App auth
- [ ] Rate limiting per API key
- [ ] Audit logging

### B. Integrations

- [ ] GitLab integration (same interface, different adapter)
- [ ] Slack notifications on metric regressions
- [ ] GitHub Actions workflow for automated weekly reports
- [ ] Webhook for real-time PR events

### C. Monitoring

- [ ] Prometheus metrics export endpoint
- [ ] Grafana dashboard for cycle time and LLM latency
- [ ] OTLP export to Jaeger / Tempo

---

## Phase 4: Advanced Analytics

### A. Visualisation

- [ ] React dashboard (cycle time charts, contributor leaderboard, trend lines)
- [ ] Date-range picker
- [ ] Team performance scorecards

### B. Machine Learning

- [ ] Anomaly detection on velocity metrics
- [ ] PR risk scoring (size, author history, review coverage)
- [ ] Burndown prediction

---

## Technical Debt

### High Priority

- [ ] Persistent circuit-breaker state (back by Redis so restarts don't reset counters)
- [ ] Background sync worker (pre-fetch metrics on a schedule instead of on-demand)
- [ ] Database for historical metrics (currently only cached for 10 minutes)

### Medium Priority

- [ ] Redis authentication for production deployments
- [ ] Restrict `allow_origins` from `"*"` to explicit list in production
- [ ] Type hints for all remaining untyped functions
- [ ] Async GitHub GraphQL client (currently sync httpx)

### Low Priority

- [ ] Consolidate sync + async circuit breakers into one implementation
- [ ] Add `examples.py` showing multi-turn conversation and judge usage

---

## Rough Timeline

| Phase | Duration | Key Deliverables |
| --- | --- | --- |
| Phase 1 | ✅ Shipped | Full API, LiteLLM, Judge, Resiliency, Docs |
| Phase 2 | 4–6 weeks | Historical DB, advanced metrics, streaming chat |
| Phase 3 | 8–12 weeks | Auth, integrations, Prometheus |
| Phase 4 | 12–16 weeks | React dashboard, ML signals |

---

## Open Questions

1. **Historical storage**: TimescaleDB vs plain PostgreSQL with a metrics table?
2. **Frontend**: React SPA or server-side HTML (HTMX)?
3. **Multi-tenancy**: per-API-key GitHub token isolation?
4. **Judge golden set**: how to version and store reference outputs?
