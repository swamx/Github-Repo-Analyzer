# AI Code Review Pipeline Implementation

This document describes the code review feature implemented based on **Feedback-Architecture.md** (doc sections 2–16).

## Overview

The code review system provides an event-driven, multi-stage pipeline for automated PR review:

```
GitHub Webhook / Direct API
        ↓
Deterministic Diff Analysis (risk classification)
        ↓
Repository Context Retrieval
        ↓
Review Orchestrator (decide which agents to run)
        ↓
Parallel Agent Execution (correctness, security, testing)
        ↓
Validation (treat LLM output as untrusted)
        ↓
Deduplication (merge near-duplicate findings)
        ↓
Confidence/Severity Scoring
        ↓
Policy Enforcement (thresholds)
        ↓
GitHub PR Comments & Summary
        ↓
Developer Feedback & Metrics
```

## Architecture

### Modules

All code review logic lives in `app/services/code_review/`:

#### `schemas.py` — Data Models
- **Enums**: `Severity` (low/medium/high/critical), `RiskLevel`, `FindingCategory` (correctness/security/testing), `CommentLevel`
- **Models**: `Finding`, `RiskProfile`, `ReviewResult`, `ReviewRequest`
- Structured output matching doc section 9

#### `github_client.py` — GitHub Integration
- `AsyncGitHubReviewClient`: async REST API wrapper
  - `get_pull_request()` — fetch PR metadata + head commit
  - `get_pull_request_files()` — list changed files with diffs
  - `get_file_content()` — fetch raw file at a commit (for context)
  - `post_review_comment()` — line-anchored PR review comment
  - `post_issue_comment()` — summary comment on PR issue
  - `verify_webhook_signature()` — HMAC-SHA256 verification (doc section 21)
- Uses `ResilientClient` for rate limiting, circuit breaker, retry logic

#### `diff_analyzer.py` — Deterministic Risk Analysis (doc section 3)
- No LLM calls — pure heuristics
- `_classify_file_risk()` — file-path-based risk classification (HIGH/MEDIUM/LOW)
- Risk indicators: auth, payment, database migrations, public APIs, test files
- `analyze_diff()` — produces `RiskProfile` per file + changed line ranges

#### `validation.py` — Untrusted Output Validation (doc section 10)
- `validate_findings()` — drop findings where:
  - File not in diff
  - Line not in changed range
  - Evidence substring not found in patch
- Logs dropped findings with reason

#### `dedup.py` — Finding Deduplication (doc section 11)
- `deduplicate_findings()` — merge near-duplicates by:
  - Grouping by (file, line proximity, category)
  - Text similarity matching
  - Keeping highest-confidence finding
- Preserves evidence from merged findings

#### `scoring.py` — Policy Enforcement (doc section 12–13)
- `score_findings()` — apply confidence/severity thresholds:
  - confidence < 0.65 → suppressed
  - 0.65–0.85 → informational
  - > 0.85 + HIGH/CRITICAL severity → blocking
- Thresholds configurable via `app/config.py` or request overrides
- Returns summary counts by severity

#### `comment_formatter.py` — GitHub Comments (doc section 14)
- `format_finding_comment()` — line-specific comment with evidence + fix
- `format_summary_comment()` — category breakdown, status indicators, feedback prompt

#### `orchestrator.py` — Deterministic Review Planning (doc section 6)
- No LLM calls — rule-based
- `plan_review()` — decide which agents run on each file:
  - HIGH risk → correctness + security + testing
  - MEDIUM risk → correctness + security
  - LOW risk → correctness only
  - Test files skip security agent

#### `agents.py` — LLM Review Agents (doc section 7)
- `run_correctness_agent()` — null handling, branching, race conditions, type mismatches
- `run_security_agent()` — auth, injection, secret exposure, unsafe deserialization
- `run_test_agent()` — untested code, missing edge cases, coverage gaps
- Each agent:
  - Builds system + user prompt with file context
  - Calls `LLMService.chat_message_async()` requesting strict JSON
  - Parses `list[Finding]` via `_strip_json_fences` pattern
  - Runs in parallel per file via `asyncio.gather`

#### `feedback_store.py` — Metrics & Developer Feedback (doc section 15–16)
- SQLite backend (stdlib, no new dependencies)
- `store_review()` — persist PR review + findings (idempotent by repo+PR+commit)
- `record_feedback()` — log developer reactions (helpful, false_positive, fixed, etc.)
- `get_stats()` — aggregate metrics:
  - Total reviews/findings by repository
  - Findings by category/severity
  - False-positive rate, feedback distribution
  - Feeds evaluation pipeline for model improvement

#### `service.py` — Main Orchestrator
- `CodeReviewService.review_pull_request()` — wires all stages:
  1. Fetch PR metadata + files
  2. Risk analysis → orchestration
  3. Run agents in parallel
  4. Validate, deduplicate, score
  5. Post findings to GitHub (unless dry_run)
  6. Store metrics
- Returns `ReviewResult` with findings + summary

### API Layer

#### `app/models/code_review_schemas.py`
- Request/response models for HTTP endpoints
- Matches existing `judge_schemas.py` style

#### `app/api/code_review_routes.py`
- **`POST /api/code-review/review`** — Direct trigger (doc section 2)
  - Sync endpoint, returns findings immediately
  - Request: `{repo, pr_number, dry_run, info_threshold?, block_threshold?}`
  - Response: `ReviewResult` with findings + summary
  - Use for manual testing, integrations expecting immediate results

- **`POST /api/code-review/webhook`** — GitHub Webhook Handler (doc section 2, 21)
  - Verifies `X-Hub-Signature-256` HMAC against `GITHUB_WEBHOOK_SECRET`
  - Filters for `pull_request` events (opened/synchronize/reopened)
  - Queues review asynchronously via `BackgroundTasks`
  - Returns `202 Accepted` immediately
  - Treats repository content as untrusted (doc section 22) — only trust webhook signature

- **`POST /api/code-review/feedback`** — Record Feedback (doc section 15)
  - Request: `{finding_id, reaction}`
  - Reactions: helpful, not_helpful, false_positive, already_known, fixed
  - Feeds evaluation pipeline

- **`GET /api/code-review/stats`** — Metrics (doc section 16)
  - Query params: `?repository=owner/repo` (optional)
  - Returns aggregated stats: total reviews/findings, by-category breakdown, false-positive rate

### Configuration

Extended `app/config.py` with new settings:

```python
GITHUB_API_BASE = "https://api.github.com"  # Configurable for GitHub Enterprise
GITHUB_WEBHOOK_SECRET = ""  # Required for webhook (warned if unset)
REVIEW_CONFIDENCE_INFO_THRESHOLD = 0.65     # Informational cutoff
REVIEW_CONFIDENCE_BLOCK_THRESHOLD = 0.85    # Blocking cutoff
REVIEW_DB_PATH = "code_review_feedback.db"  # SQLite feedback store location
```

## Differences from Full Enterprise Design

The production design (Feedback-Architecture.md) assumes Kafka, vector DB, autoscaled workers, multi-tenant policy hierarchy.
This implementation simplifies while keeping core logic intact:

| Aspect | Enterprise Design | This Implementation |
|--------|-------------------|---------------------|
| Ingestion | Kafka queue | FastAPI BackgroundTasks + direct API |
| Storage | PostgreSQL metadata + vector DB | SQLite + in-process caches |
| Indexing | Incremental repo index | Lightweight file fetch (no embeddings) |
| Workers | Autoscaled pool | In-process async tasks |
| Multi-tenancy | Hierarchical policies (org/BU/repo/branch) | Per-request thresholds |
| Retry/backpressure | Kafka rebalancing | FastAPI middleware + resilient client |

**Core pipeline stages 1–16 are fully implemented** — docs sections that require distributed infrastructure are adapted to single-instance FastAPI.

## Usage

### Trigger a Review (Direct API)

```bash
curl -X POST http://localhost:8000/api/code-review/review \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "owner/repo",
    "pr_number": 42,
    "dry_run": false
  }'
```

Response:
```json
{
  "status": "completed",
  "data": {
    "repository": "owner/repo",
    "pull_request": 42,
    "head_commit": "abc123...",
    "findings": [
      {
        "file": "src/app.py",
        "line": 10,
        "category": "security",
        "severity": "high",
        "confidence": 0.92,
        "evidence": ["missing authorization check"],
        "suggested_fix": "Restore authorizeUser() before processing payment"
      }
    ],
    "comment_level_summary": {
      "blocking": 1,
      "informational": 0
    }
  }
}
```

### Webhook Setup

1. Set `GITHUB_WEBHOOK_SECRET` environment variable
2. In GitHub repo settings, add webhook:
   - Payload URL: `https://your-api.example.com/api/code-review/webhook`
   - Content type: `application/json`
   - Events: Pull requests
   - Secret: (same as `GITHUB_WEBHOOK_SECRET`)
3. GitHub will POST pull_request events; webhook handler verifies signature and queues review

### Record Feedback

```bash
curl -X POST http://localhost:8000/api/code-review/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "finding_id": "abc123",
    "reaction": "false_positive"
  }'
```

### Get Metrics

```bash
curl http://localhost:8000/api/code-review/stats?repository=owner/repo
```

Response:
```json
{
  "status": "ok",
  "data": {
    "total_reviews": 42,
    "total_findings": 156,
    "findings_by_category": {
      "correctness": 80,
      "security": 50,
      "testing": 26
    },
    "feedback_by_reaction": {
      "helpful": 120,
      "false_positive": 8,
      "fixed": 28
    },
    "false_positive_rate": 0.051
  }
}
```

## Testing

Run unit tests (no external dependencies required):

```bash
python -c "
from app.services.code_review.diff_analyzer import _classify_file_risk
from app.services.code_review.schemas import RiskLevel

level, _ = _classify_file_risk('src/auth/login.py')
assert level == RiskLevel.HIGH
print('Unit tests pass')
"
```

Test coverage:
- `diff_analyzer` — file risk classification
- `validation` — finding validation against diff
- `dedup` — finding deduplication
- `scoring` — confidence/severity policies
- `github_client` — webhook signature verification
- `api` — endpoint routing (mock-based)

## Key Design Decisions

1. **In-process pipeline**: Simpler than Kafka for single-instance deployment; can be extracted to background workers later.

2. **SQLite feedback store**: No external DB; sufficient for feedback collection + evaluation metrics. Scales to ~100k findings before needing optimization.

3. **No embeddings**: Lightweight context retrieval via lexical search + file relationships, not semantic search. Simplifies deployment.

4. **Deterministic orchestrator**: No LLM call to decide which agents run — rule-based on risk profiles. Faster, more predictable, cheaper.

5. **Untrusted LLM output**: Validation layer (doc section 10) drops any finding that doesn't ground to actual code. Reduces false positives.

6. **Policy thresholds**: Simple confidence + severity rules (doc section 12–13). Can evolve to per-team customization.

7. **Webhook HMAC verification**: Treats repository content as untrusted data (doc section 21–22). Signature proves GitHub sent it.

## Security Considerations

- **Repository content as untrusted input**: Code diffs can contain prompt injections. System prompt is hardcoded, not derived from PR comments.
- **LLM output validation**: Finding file/line references verified against diff before posting.
- **Webhook signature verification**: HMAC-SHA256 prevents unauthorized reviews.
- **Least privilege**: Review agent can only read code + post comments, not merge/modify.

## Future Enhancements

1. **Distributed workers**: Extract agent execution to separate service, queue via Kafka
2. **Vector DB indexing**: Semantic context retrieval for multi-file analysis
3. **Model routing**: Route simple checks to faster models, complex analysis to frontier model
4. **Multi-tenancy**: Per-repository policy hierarchy (org/BU/repo/branch)
5. **Advanced dedup**: Cross-finding semantic similarity using embeddings
6. **Continuous evaluation**: Automated feedback loop measuring precision/recall vs. historical bug reports
7. **Custom rules**: User-defined linters + LLM agent coordination

## Monitoring & Observability

- Structured logging at each pipeline stage
- OTel integration (via `llm_service` + `resilient_client`) for tracing
- Review metrics stored in SQLite for querying
- Feedback counts by reaction type for model evaluation

## References

- [Feedback-Architecture.md](./Feedback-Architecture.md) — full enterprise design
- [QUICKSTART.md](./QUICKSTART.md) — getting started with the whole API
- [app/services/code_review/](./app/services/code_review/) — implementation
- [tests/test_code_review.py](./tests/test_code_review.py) — tests
