# Documentation Index

## Core Features

### Code Review System
- **[CODE_REVIEW.md](./CODE_REVIEW.md)** — AI-powered PR review pipeline
  - Multi-agent review (correctness, security, testing)
  - Webhook + direct API endpoints
  - Feedback collection & metrics

### Architecture & Design
- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — System design (reference)
- **[FEEDBACK_ARCHITECTURE.md](./FEEDBACK_ARCHITECTURE.md)** — Enterprise design template

## Getting Started

### First Time?
1. Read the main [README.md](../README.md)
2. Follow [QUICKSTART.md](./QUICKSTART.md)
3. Test locally with `python main.py`

### Integrating Code Review?
1. Read [CODE_REVIEW.md](./CODE_REVIEW.md) — Architecture & usage
2. Set up webhooks (see Code Review section)
3. Test with `/api/code-review/review` endpoint

## API Reference
- **Code Review** — `/api/code-review/review`, `/api/code-review/webhook`, `/api/code-review/stats`
- **Swagger Docs** — Start server, visit `http://localhost:8000/docs`

## Configuration
- Environment variables in `.env` file
- See sections below for code review specific config

---

See individual docs for detailed information.
