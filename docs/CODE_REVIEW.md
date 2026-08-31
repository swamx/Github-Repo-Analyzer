# Code Review System

AI-powered PR review using multi-agent analysis (correctness, security, testing) with developer feedback collection.

## Quick Start

### 1. Test Locally
```bash
# Start server
python main.py

# Trigger a review (dry-run)
curl -X POST http://localhost:8000/api/code-review/review \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/repo", "pr_number": 42, "dry_run": true}'
```

### 2. Set Up Webhook
```bash
# Set secret
export GITHUB_WEBHOOK_SECRET="your-secret"

# In GitHub repo settings → Webhooks:
# - Payload URL: https://your-domain.com/api/code-review/webhook
# - Secret: (same as above)
# - Events: Pull requests
```

### 3. Record Feedback
```bash
curl -X POST http://localhost:8000/api/code-review/feedback \
  -H "Content-Type: application/json" \
  -d '{"finding_id": "xyz123", "reaction": "false_positive"}'
```

### 4. View Metrics
```bash
curl http://localhost:8000/api/code-review/stats?repository=owner/repo
```

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/code-review/review` | Trigger review (returns findings immediately) |
| POST | `/api/code-review/webhook` | GitHub webhook handler (async) |
| POST | `/api/code-review/feedback` | Record developer reaction to finding |
| GET | `/api/code-review/stats` | Review metrics & accuracy stats |

## Review Findings

Each finding includes:
- **file** — File path
- **line** — Line number
- **category** — correctness, security, or testing
- **severity** — low, medium, high, critical
- **confidence** — 0.0–1.0 score
- **evidence** — Code snippets supporting the finding
- **suggested_fix** — Actionable recommendation

## Policy Thresholds

Default confidence thresholds:
- < 0.65 → suppressed (not posted)
- 0.65–0.85 → informational comment
- > 0.85 + HIGH/CRITICAL severity → blocking review

Override per request:
```bash
curl -X POST http://localhost:8000/api/code-review/review \
  -d '{"repo": "owner/repo", "pr_number": 42, "info_threshold": 0.6, "block_threshold": 0.9}'
```

## Environment Variables

```bash
# Required
GITHUB_TOKEN=ghp_xxxxx

# Optional (code review)
GITHUB_API_BASE=https://api.github.com           # GitHub Enterprise compatible
GITHUB_WEBHOOK_SECRET=your-webhook-secret        # Required for webhooks
REVIEW_CONFIDENCE_INFO_THRESHOLD=0.65            # Informational cutoff
REVIEW_CONFIDENCE_BLOCK_THRESHOLD=0.85           # Blocking cutoff
REVIEW_DB_PATH=code_review_feedback.db           # Feedback store
```

## Architecture

Pipeline stages:

```
Input (Webhook/API)
  ↓
Diff Analysis (risk classification: HIGH/MEDIUM/LOW)
  ↓
Orchestration (decide which agents to run — no LLM)
  ↓
Agents (correctness, security, testing — parallel LLM calls)
  ↓
Validation (drop findings not grounded in diff)
  ↓
Deduplication (merge similar findings)
  ↓
Scoring (apply confidence/severity thresholds)
  ↓
GitHub Comments (line-anchored + summary)
  ↓
Feedback Store (metrics & evaluation)
```

### Components

| Module | Purpose |
|--------|---------|
| `diff_analyzer` | Risk classification by file heuristics |
| `orchestrator` | Determine which agents to run (rule-based) |
| `agents` | Correctness, security, testing LLM agents |
| `validation` | Validate findings against diff |
| `dedup` | Merge near-duplicate findings |
| `scoring` | Apply policy thresholds |
| `github_client` | GitHub REST API + webhook verification |
| `comment_formatter` | Render findings as comments |
| `feedback_store` | SQLite metrics & reactions |
| `service` | Main orchestrator |

All in `app/services/code_review/`.

## Response Examples

### Finding (line-anchored comment on GitHub)
```
**Security Issue**: HIGH

**Evidence**:
- missing authorization check

**Suggestion**: Restore authorizeUser() before processing payment

Agent notes: Payment endpoint no longer verifies user identity
```

### Summary Comment
```
## AI Code Review Summary

**Status**: ⚠️ Blocking Issues Found

**Total findings**: 3

**By category**:
- ⚠️ **Security**: 1 finding (1 blocking)
- ℹ️ **Correctness**: 2 findings
- ✓ **Testing**: 0 findings
```

## Feedback Reactions

```json
{
  "finding_id": "abc123",
  "reaction": "helpful"  // or: not_helpful, false_positive, already_known, fixed
}
```

## Metrics

```json
{
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
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No findings | Check LLM is running; try PR with larger diff |
| Webhook not triggering | Verify `GITHUB_WEBHOOK_SECRET` matches; check GitHub webhook delivery log |
| Signature verification failed | Secret must match exactly (no trailing spaces) |
| Files not found | Changed files must be in PR diff |

## Security

- ✅ Webhook requests verified with HMAC-SHA256
- ✅ Findings validated against diff (blocks hallucinations)
- ✅ System prompt hardcoded (not derived from repo content)
- ✅ Least-privilege agent (read-only + comment)

## Testing

```bash
# Unit tests
pytest tests/test_code_review.py -v

# Manual test
curl -X POST http://localhost:8000/api/code-review/review \
  -d '{"repo": "owner/repo", "pr_number": 1, "dry_run": true}'
```

---

For architecture details, see [FEEDBACK_ARCHITECTURE.md](./FEEDBACK_ARCHITECTURE.md).
