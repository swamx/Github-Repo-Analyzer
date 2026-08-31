# Quick Start Guide

## 5-Minute Setup

### 1. Install & Configure
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (.env)
export GITHUB_TOKEN=ghp_xxxxx
export LITELLM_API_KEY=xxxxx
export PRIMARY_MODEL=claude-haiku
```

### 2. Start Server
```bash
python main.py
```

Visit `http://localhost:8000/docs` for interactive API docs.

### 3. Test Code Review
```bash
# Dry run (no GitHub posting)
curl -X POST http://localhost:8000/api/code-review/review \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "owner/repo",
    "pr_number": 42,
    "dry_run": true
  }'
```

Expected: JSON with findings, summaries, confidence scores.

## Setting Up Webhooks

### For Production

1. **Set webhook secret** (25+ random chars)
   ```bash
   export GITHUB_WEBHOOK_SECRET="super-secret-xyz123..."
   ```

2. **Expose API publicly** (ngrok for testing)
   ```bash
   ngrok http 8000
   # → https://abc123.ngrok.io/api/code-review/webhook
   ```

3. **Add to GitHub** (Repo → Settings → Webhooks)
   - Payload URL: `https://your-domain.com/api/code-review/webhook`
   - Content type: `application/json`
   - Secret: (match `GITHUB_WEBHOOK_SECRET`)
   - Events: Pull requests only
   - Active: ✓

4. **Test**
   - Create/push a PR
   - Check server logs for `Webhook: queuing review`
   - Review comments should appear on PR within 1–2 minutes

## Recording Feedback

After review comments appear on a PR, developers can provide feedback:

```bash
# Get finding_id from GitHub comment or review logs
curl -X POST http://localhost:8000/api/code-review/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "finding_id": "abc123",
    "reaction": "helpful"
  }'
```

Reactions: `helpful`, `not_helpful`, `false_positive`, `already_known`, `fixed`

## Viewing Metrics

```bash
# All repos
curl http://localhost:8000/api/code-review/stats

# Single repo
curl http://localhost:8000/api/code-review/stats?repository=owner/repo
```

Returns: Review counts, findings by category, false-positive rate, reaction distribution.

## Customizing Thresholds

Override per-request:

```bash
curl -X POST http://localhost:8000/api/code-review/review \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "owner/repo",
    "pr_number": 42,
    "info_threshold": 0.55,      # Lower = more comments
    "block_threshold": 0.90       # Higher = fewer blocking
  }'
```

Or environment variables:
```bash
export REVIEW_CONFIDENCE_INFO_THRESHOLD=0.60
export REVIEW_CONFIDENCE_BLOCK_THRESHOLD=0.80
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No module named pytest" | `pip install pytest` |
| LLM connection error | Verify `LITELLM_API_BASE` and `LITELLM_API_KEY` |
| Webhook 401 Unauthorized | `GITHUB_WEBHOOK_SECRET` mismatch |
| No findings | Check PR has code changes; try a larger diff |

## Next Steps

1. ✅ Run locally
2. ✅ Test `/api/code-review/review` endpoint
3. ✅ Set up webhook
4. ✅ Push a test PR
5. ✅ Collect feedback for 1–2 weeks
6. ✅ Check `/stats` to measure accuracy

---

See [CODE_REVIEW.md](./CODE_REVIEW.md) for full documentation.
