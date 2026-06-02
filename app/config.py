import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 600))

    # LLM model routing via LiteLLM proxy
    # PRIMARY_MODEL  — served by LiteLLM as "claude-haiku"
    # SECONDARY_MODEL — LiteLLM router fallback "ollama-llama3"
    PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "claude-haiku")
    SECONDARY_MODEL = os.getenv("SECONDARY_MODEL", "ollama-llama3")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    LLM_RATE_LIMIT = int(os.getenv("LLM_RATE_LIMIT", "10"))
    LLM_RATE_PERIOD = float(os.getenv("LLM_RATE_PERIOD", "1"))

    # LiteLLM proxy connection
    LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "http://localhost:4000")
    LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")   # must equal LITELLM_MASTER_KEY

    # Circuit breaker for LiteLLM proxy calls
    LITELLM_FAILURE_THRESHOLD = int(os.getenv("LITELLM_FAILURE_THRESHOLD", "3"))
    LITELLM_FAILURE_RECOVERY_SECONDS = int(os.getenv("LITELLM_FAILURE_RECOVERY_SECONDS", "60"))

    # API metadata
    API_TITLE = "GitHub Engineering Intelligence API"
    API_VERSION = "2.0.0"
    API_DESCRIPTION = "AI-powered GitHub analytics with LangGraph agent interface"

    @staticmethod
    def validate():
        if not Settings.GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN environment variable not set")
        if not Settings.LITELLM_API_KEY:
            raise ValueError("LITELLM_API_KEY environment variable not set")


settings = Settings()
