from pydantic_settings import BaseSettings


class IssuePilotSettings(BaseSettings):
    # GitHub
    GITHUB_TOKEN: str = ""
    GITHUB_API_BASE: str = "https://api.github.com"

    # Google ADK (coordinator agent — Gemini)
    GOOGLE_API_KEY: str = ""
    COORDINATOR_MODEL: str = "gemini-2.0-flash"   # fast orchestration model

    # Anthropic (used by Claude Code workers via ANTHROPIC_API_KEY env var)
    ANTHROPIC_API_KEY: str = ""

    # lean-ctx 3.0 MCP server command (adjust if installed differently)
    # Examples:
    #   "npx -y lean-ctx"            (npx, no global install)
    #   "lean-ctx"                   (global npm install)
    #   "python -m lean_ctx.server"  (Python package)
    LEAN_CTX_CMD: str = "npx -y lean-ctx"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_STREAM: str = "issue_pilot:jobs"
    REDIS_GROUP: str = "workers"
    REDIS_CONSUMER: str = "worker-1"
    JOB_TTL_SECONDS: int = 86400  # 24 h

    # Claude Code worker limits
    WORKER_TIMEOUT_S: int = 600   # 10 min per issue before killing subprocess
    MAX_PARALLEL_WORKERS: int = 5 # hard cap on simultaneous claude subprocesses

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = IssuePilotSettings()
