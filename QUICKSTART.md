# Quick Start

Get the GitHub Engineering Intelligence API running in under 5 minutes.

---

## Prerequisites

- Docker and Docker Compose
- GitHub Personal Access Token — [create one here](https://github.com/settings/tokens)
  (scopes: `repo`, `read:user`)
- Anthropic API key — [create one here](https://console.anthropic.com/settings/keys)

---

## 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
GITHUB_TOKEN=your_github_pat
ANTHROPIC_API_KEY=your_anthropic_key
LITELLM_MASTER_KEY=any-secret-string
```

`LITELLM_MASTER_KEY` is the internal auth key between the API and the LiteLLM proxy.
Pick any string — it never leaves your machine.

---

## 2. Start all services

```bash
docker-compose up --build
```

First run pulls the Ollama llama3 model (~4 GB) as a fallback. Subsequent starts are fast.
Services start in dependency order: postgres → litellm → api.

---

## 3. Verify health

```bash
curl http://localhost:8000/api/health
```

Expected:

```json
{ "status": "healthy", "components": { "llm": { "status": "healthy" } } }
```

---

## 4. Open the docs

| URL | What it is |
| --- | --- |
| <http://localhost:8000/docs> | Swagger UI — interactive API docs |
| <http://localhost:4000/ui> | LiteLLM UI — request logs, spend, model latency |
| <http://localhost:3001> | Open WebUI — chat directly with Ollama |

---

## First API calls

### Analyze a repository

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/microsoft/vscode",
    "start_time": "2024-01-01T00:00:00Z",
    "end_time": "2024-03-31T23:59:59Z"
  }'
```

Returns engineering metrics (cycle time, review latency, velocity, contributors)
plus an LLM-generated executive summary with key findings and recommendations.

### Chat about the data

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Who are the top contributors and is review latency a bottleneck?",
    "repo_url": "https://github.com/microsoft/vscode"
  }'
```

The LLM receives the full metrics context and cites specific numbers in its answer.

### Metrics only (no LLM)

```bash
curl "http://localhost:8000/api/metrics?repo_url=https://github.com/microsoft/vscode"
```

---

## Try the LLM Judge

Compare two prompt strategies against the same input. The Swagger page has this
pre-filled — just open `/docs`, find `POST /api/judge/compare`, and click **Try it out**.

```bash
curl -X POST http://localhost:8000/api/judge/compare \
  -H "Content-Type: application/json" \
  -d '{
    "input": "42 PRs merged, avg cycle time 68h, avg review latency 18h, 8 contributors, 4 reviewers. Top contributor merged 14 PRs.",
    "variants": [
      {
        "name": "concise-analyst",
        "description": "3-bullet executive summary style — scannable snapshot",
        "system_prompt": "You are a senior engineering metrics analyst. Give a 3-bullet executive summary: what is good, what needs attention, and the single most important action item. Be direct and data-driven.",
        "user_prompt": "{input}",
        "model": "claude-haiku",
        "temperature": 0.2
      },
      {
        "name": "detailed-coach",
        "description": "Coaching style — explains why and proposes improvements",
        "system_prompt": "You are an engineering team coach. Analyse the metrics, explain what each number means in practical terms, identify bottlenecks, and suggest two specific process improvements with expected impact.",
        "user_prompt": "Here are our team metrics from last month:\n\n{input}\n\nWhat should we focus on improving?",
        "model": "claude-haiku",
        "temperature": 0.4
      }
    ],
    "judge_model": "claude-haiku",
    "criteria": ["accuracy", "helpfulness", "clarity", "conciseness"],
    "runs_per_variant": 1
  }'
```

The judge scores each output 0–1 on each criterion (blind — it sees Output A / Output B,
not the variant names) and picks a winner.

---

## Local Python setup (without Docker)

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

You still need LiteLLM and Redis running. The easiest way is to start just those
services from Docker Compose:

```bash
docker-compose up redis litellm postgres -d
```

Then:

```bash
export GITHUB_TOKEN=your_pat
export LITELLM_API_BASE=http://localhost:4000
export LITELLM_API_KEY=your_litellm_master_key
uvicorn main:app --reload
```

---

---

## IssuePilot — autonomous issue fixer

IssuePilot needs three extra keys and a second process alongside the API server.

### 1. Add IssuePilot env vars

```env
# .env (append to existing)
GOOGLE_API_KEY=AIza...          # coordinator (Gemini 2.0 Flash)
ANTHROPIC_API_KEY=sk-ant-...    # Claude Code workers
LEAN_CTX_CMD=npx -y lean-ctx   # lean-ctx 3.0 — adjust if installed differently
GITHUB_TOKEN=ghp_...            # needs repo + write scopes for PR creation
```

### 2. Start the pipeline worker

In a second terminal (the API server must already be running):

```bash
python -m issue_pilot.pipeline.worker
```

You should see:

```text
INFO issue_pilot.pipeline.worker IssuePilot pipeline worker started
```

### 3. Submit issues to fix

```bash
curl -X POST http://localhost:8000/api/issue-pilot/fix \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/your-org/your-repo",
    "issue_numbers": [42, 57],
    "create_pr": true,
    "base_branch": "main"
  }'
```

Response (HTTP 202):

```json
{
  "job_id": "a3f8c2d1e9b04f...",
  "status": "queued",
  "status_url": "/api/issue-pilot/status/a3f8c2d1e9b04f..."
}
```

### 4. Poll for results

```bash
# repeat until status is "done" or "failed"
curl http://localhost:8000/api/issue-pilot/status/a3f8c2d1e9b04f...
```

When done:

```json
{
  "status": "done",
  "issue_plans": [
    { "issue_number": 42, "title": "...", "plan": "## Fix Plan..." },
    { "issue_number": 57, "title": "...", "plan": "## Fix Plan..." }
  ],
  "pr_url": "https://github.com/your-org/your-repo/pull/123"
}
```

### What happens under the hood

1. Job enqueued → Redis Stream
2. Pipeline worker reads job → starts Google ADK coordinator (Gemini)
3. Coordinator fans out one `claude` CLI subprocess per issue (parallel)
4. Each Claude Code worker: runs lean-ctx 3.0, detects tech stack, loads skills, researches issue, writes fix, commits
5. Coordinator collects results → creates PRs → marks job done

---

## Running tests

```bash
export GITHUB_TOKEN=test_token
pytest tests/ -v
```

---

## Common use cases

### Last 30 days

```python
from datetime import datetime, timedelta, timezone
import requests

end = datetime.now(timezone.utc)
start = end - timedelta(days=30)

r = requests.post("http://localhost:8000/api/analyze", json={
    "repo_url": "https://github.com/your-org/your-repo",
    "start_time": start.isoformat(),
    "end_time": end.isoformat(),
})
m = r.json()["metrics"]
print(f"Cycle time: {m['avg_cycle_time_hours']:.1f}h  Quality: {m['quality_score']:.2f}")
```

### Multi-turn conversation

```python
import requests

base = "http://localhost:8000/api/chat"
repo = "https://github.com/microsoft/vscode"

r1 = requests.post(base, json={"message": "Summarise velocity trends", "repo_url": repo})
history = [
    {"role": "user",      "content": "Summarise velocity trends"},
    {"role": "assistant", "content": r1.json()["message"]},
]

r2 = requests.post(base, json={
    "message": "Which contributors drive that trend the most?",
    "repo_url": repo,
    "conversation_history": history,
})
print(r2.json()["message"])
```

---

## Troubleshooting

**`401 Unauthorized`** — `LITELLM_API_KEY` must equal `LITELLM_MASTER_KEY`.
Both are wired automatically in `docker-compose.yml`.

**`Failed to parse LLM response as JSON`** — container not rebuilt after a code change.
Run `docker-compose up --build api`.

**Port 3001 in use** — change `"3001:8080"` in `docker-compose.yml` to an open port.

**Ollama pulling slowly** — first run downloads ~4 GB. Check progress:
`docker logs ollama-init -f`

**LiteLLM never healthy** — healthcheck uses Python urllib, not curl. Do not replace it.
