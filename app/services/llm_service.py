import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any

import httpx

from app.config import settings
from app.models.schemas import RepositoryMetrics, AnalysisSummary
from app.services.cache_service import CacheService
from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenException
from app.services.resilient_client import ResilientClient

logger = logging.getLogger(__name__)


class LLMService:
    """Service for LLM-powered analysis and summarization"""

    def __init__(self):
        self.cache = CacheService()
        self.primary_model = settings.PRIMARY_MODEL
        # Claude HTTP endpoint (local) settings
        self.litellm_api_key = settings.LITELLM_API_KEY
        self.litellm_api_base = settings.LITELLM_API_BASE
        self.litellm_circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=settings.LITELLM_FAILURE_THRESHOLD,
                recovery_timeout=settings.LITELLM_FAILURE_RECOVERY_SECONDS,
                name="claude"
            )
        )
        self.resilient_client = ResilientClient(
            rate_limit=settings.LLM_RATE_LIMIT,
            per_seconds=settings.LLM_RATE_PERIOD,
            fail_max=settings.LITELLM_FAILURE_THRESHOLD,
            reset_timeout=settings.LITELLM_FAILURE_RECOVERY_SECONDS,
        )
        self.last_backend_used = None
        logger.info(
            "LLMService initialized primary_model=%s secondary_model=%s claude_api_base=%s",
            self.primary_model,
            settings.SECONDARY_MODEL,
            self.litellm_api_base,
        )

    def summarize_metrics(
        self,
        metrics: RepositoryMetrics,
        use_cache: bool = True
    ) -> AnalysisSummary:
        """Generate LLM-powered summary of metrics"""
        cache_key = f"summary:{metrics.owner}:{metrics.repo}:{metrics.analysis_period}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return AnalysisSummary(**cached)

        prompt = self._build_summary_prompt(metrics)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert software engineering analytics assistant. "
                    "Analyze GitHub repository metrics and provide insightful analysis. "
                    "IMPORTANT: your entire response must be a single valid JSON object. "
                    "Do NOT include prose, markdown, or code fences. "
                    "Start your response with { and end with }."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        logger.debug("summarize_metrics cache_key=%s use_cache=%s", cache_key, use_cache)
        try:
            content, backend = self._execute_chat(messages, temperature=settings.LLM_TEMPERATURE, operation="summarize_metrics")
            self.last_backend_used = backend
            logger.info("summarize_metrics succeeded backend=%s content_len=%d", backend, len(content))
            if not content or not content.strip():
                raise ValueError(
                    "LLM returned empty content. This usually means LiteLLM routed to tool_calls "
                    "internally (Claude + response_format). Ensure response_format is NOT in the "
                    "payload and rebuild the container."
                )
            logger.debug("summarize_metrics raw content=%r", content[:300])
            parsed = json.loads(self._strip_json_fences(content))

            summary = AnalysisSummary(
                summary=parsed.get("summary", ""),
                key_findings=parsed.get("key_findings", []),
                performance_insights=parsed.get("performance_insights", {}),
                root_cause_hypotheses=parsed.get("root_cause_hypotheses", []),
                recommendations=parsed.get("recommendations", []),
                confidence_score=parsed.get("confidence_score", 0.8)
            )

            self.cache.set(cache_key, summary.model_dump())
            return summary

        except json.JSONDecodeError as e:
            logger.warning("summarize_metrics non-JSON response (len=%d): %s", len(content), e)
            raise ValueError(content) from e
        except Exception as e:
            raise RuntimeError(f"LLM analysis failed: {e}") from e

    def analyze_metrics(
        self,
        metrics: RepositoryMetrics,
        question: Optional[str] = None
    ) -> str:
        """Analyze metrics and answer a specific question"""
        prompt = f"""
        Analyze the following GitHub repository metrics:
        
        Repository: {metrics.owner}/{metrics.repo}
        Analysis Period: {metrics.analysis_period}
        
        Key Metrics:
        - Total PRs Merged: {metrics.total_prs_merged}
        - Total Issues Closed: {metrics.total_issues_closed}
        - Total Reviews: {metrics.total_reviews}
        - Average Cycle Time: {metrics.avg_cycle_time_hours:.1f} hours
        - Median Cycle Time: {metrics.median_cycle_time_hours:.1f} hours
        - Average Review Latency: {metrics.avg_review_latency_hours:.1f} hours
        - Unique Contributors: {metrics.unique_contributors}
        - Unique Reviewers: {metrics.unique_reviewers}
        - Velocity Trend: {metrics.velocity_trend}
        - Quality Score: {metrics.quality_score:.2f}
        
        Top Contributors:
        {self._format_engineers(metrics.top_contributors)}
        
        Top Reviewers:
        {self._format_engineers(metrics.top_reviewers)}
        """

        if question:
            prompt += f"\n\nSpecific Question: {question}"
        else:
            prompt += "\n\nProvide a concise analysis of the most interesting patterns and insights."

        messages = [
            {
                "role": "system",
                "content": "You are an expert software engineering analytics assistant."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        logger.debug("analyze_metrics start question=%s", question)
        try:
            content, backend = self._execute_chat(messages, temperature=0.3, operation="analyze_metrics")
            self.last_backend_used = backend
            logger.info("analyze_metrics succeeded backend=%s", backend)
            return content

        except Exception as e:
            logger.error("analyze_metrics failed: %s", e)
            raise RuntimeError(f"Metric analysis failed: {e}") from e

    def chat_message(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[list] = None
    ) -> str:
        """Process a chat message with context"""
        messages = []

        messages.append({
            "role": "system",
            "content": self._build_chat_system_prompt(context)
        })

        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        message = self.input_guardrail(message)
        logger.debug("chat_message request message=%s context_present=%s history_length=%s", message, bool(context), len(conversation_history) if conversation_history else 0)
        messages.append({
            "role": "user",
            "content": message
        })

        try:
            content, backend = self._execute_chat(messages, temperature=0.5, operation="chat")
            self.last_backend_used = backend
            logger.info("chat_message succeeded backend=%s", backend)
            return content

        except Exception as e:
            logger.error("chat_message failed: %s", e)
            raise RuntimeError(f"Chat message failed: {e}") from e

    def input_guardrail(self, message: str) -> str:
        """Validate and normalize incoming user text."""
        if not isinstance(message, str) or not message.strip():
            logger.warning("input_guardrail rejected empty message")
            raise ValueError("Input message cannot be empty")
        normalized = message.strip()
        logger.debug("input_guardrail normalized message=%s", normalized)
        return normalized

    def _execute_chat(self, messages: list, temperature: float, response_format: Optional[Dict[str, Any]] = None, operation: str = "llm_call") -> tuple[str, str]:
        """Execute chat via LiteLLM proxy. Fallback to secondary model is handled by the LiteLLM router."""
        logger.debug("_execute_chat start primary=%s temperature=%s operation=%s", self.primary_model, temperature, operation)
        content = self.litellm_circuit_breaker.call(
            self._generate_with_litellm,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            model_name=self.primary_model,
            operation=operation,
        )
        logger.info("_execute_chat completed primary=%s operation=%s", self.primary_model, operation)
        return content, "primary"

    async def _execute_chat_async(self, messages: list, temperature: float, response_format: Optional[Dict[str, Any]] = None, operation: str = "llm_call") -> tuple[str, str]:
        """Execute async chat via LiteLLM proxy. Fallback to secondary model is handled by the LiteLLM router."""
        logger.debug("_execute_chat_async start primary=%s temperature=%s operation=%s", self.primary_model, temperature, operation)

        async def primary_call() -> str:
            return await self._generate_with_litellm_async(
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                model_name=self.primary_model,
                operation=operation,
            )

        content = await self.resilient_client.call("claude_chat", primary_call)
        logger.info("_execute_chat_async completed primary=%s operation=%s", self.primary_model, operation)
        return content, "primary"

    async def summarize_metrics_async(
        self,
        metrics: RepositoryMetrics,
        use_cache: bool = True
    ) -> AnalysisSummary:
        """Async version of summarize_metrics for use in async API flows."""
        cache_key = f"summary:{metrics.owner}:{metrics.repo}:{metrics.analysis_period}"

        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                return AnalysisSummary(**cached)

        prompt = self._build_summary_prompt(metrics)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert software engineering analytics assistant. "
                    "Analyze GitHub repository metrics and provide insightful analysis. "
                    "IMPORTANT: your entire response must be a single valid JSON object. "
                    "Do NOT include prose, markdown, or code fences. "
                    "Start your response with { and end with }."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        logger.debug("summarize_metrics_async cache_key=%s use_cache=%s", cache_key, use_cache)
        try:
            content, backend = await self._execute_chat_async(messages, temperature=settings.LLM_TEMPERATURE, operation="summarize_metrics")
            self.last_backend_used = backend
            logger.info("summarize_metrics_async succeeded backend=%s content_len=%d", backend, len(content))
            if not content or not content.strip():
                raise ValueError(
                    "LLM returned empty content. This usually means LiteLLM routed to tool_calls "
                    "internally (Claude + response_format). Ensure response_format is NOT in the "
                    "payload and rebuild the container."
                )
            logger.debug("summarize_metrics_async raw content=%r", content[:300])
            parsed = json.loads(self._strip_json_fences(content))

            summary = AnalysisSummary(
                summary=parsed.get("summary", ""),
                key_findings=parsed.get("key_findings", []),
                performance_insights=parsed.get("performance_insights", {}),
                root_cause_hypotheses=parsed.get("root_cause_hypotheses", []),
                recommendations=parsed.get("recommendations", []),
                confidence_score=parsed.get("confidence_score", 0.8)
            )

            self.cache.set(cache_key, summary.model_dump())
            return summary

        except json.JSONDecodeError as e:
            logger.warning("summarize_metrics_async non-JSON response (len=%d): %s", len(content), e)
            raise ValueError(content) from e
        except Exception as e:
            raise RuntimeError(f"LLM analysis failed: {e}") from e

    async def chat_message_async(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[list] = None
    ) -> str:
        """Async version of chat_message for async API endpoints."""
        messages = []

        system_content = self._build_chat_system_prompt(context)
        messages.append({
            "role": "system",
            "content": system_content
        })

        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        message = self.input_guardrail(message)
        messages.append({
            "role": "user",
            "content": message
        })

        logger.debug("chat_message_async request message=%s context_present=%s history_length=%s", message, bool(context), len(conversation_history) if conversation_history else 0)
        try:
            content, backend = await self._execute_chat_async(messages, temperature=0.5, operation="chat")
            self.last_backend_used = backend
            logger.info("chat_message_async succeeded backend=%s", backend)
            return content

        except Exception as e:
            logger.error("chat_message_async failed: %s", e)
            raise RuntimeError(f"Chat message failed: {e}") from e

    def _generate_with_litellm(
        self,
        messages: list,
        temperature: float,
        response_format: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        operation: str = "llm_call",
    ) -> str:
        """Synchronous POST to the LiteLLM proxy (/v1/chat/completions)."""
        chosen_model = model_name or self.primary_model
        logger.debug("_generate_with_litellm model=%s temperature=%s", chosen_model, temperature)

        if isinstance(chosen_model, str) and chosen_model.startswith("ollama/"):
            ollama_model = chosen_model.replace("ollama/", "")
            ollama_url = f"http://{settings.OLLAMA_HOST}:{settings.OLLAMA_PORT}/v1/chat/completions"
            payload = {"model": ollama_model, "messages": messages, "temperature": temperature, "stream": False}
            resp = httpx.post(ollama_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30.0)
            resp.raise_for_status()
            return self._extract_response_content(resp.json())

        payload: Dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "temperature": temperature,
            "user": "github-analyzer-api",
            "metadata": {
                "source": "github-analyzer-api",
                "service_version": settings.API_VERSION,
                "operation": operation,
            },
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.litellm_api_key}",
        }
        resp = httpx.post(self.litellm_api_base.rstrip("/") + "/v1/chat/completions", json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return self._extract_response_content(resp.json())

    async def _generate_with_litellm_async(
        self,
        messages: list,
        temperature: float,
        response_format: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        operation: str = "llm_call",
    ) -> str:
        """Async POST to the LiteLLM proxy (/v1/chat/completions)."""
        chosen_model = model_name or self.primary_model
        logger.debug("_generate_with_litellm_async model=%s temperature=%s", chosen_model, temperature)

        if isinstance(chosen_model, str) and chosen_model.startswith("ollama/"):
            ollama_model = chosen_model.replace("ollama/", "")
            ollama_url = f"http://{settings.OLLAMA_HOST}:{settings.OLLAMA_PORT}/v1/chat/completions"
            payload = {"model": ollama_model, "messages": messages, "temperature": temperature, "stream": False}
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(ollama_url, json=payload, headers={"Content-Type": "application/json"})
                resp.raise_for_status()
                return self._extract_response_content(resp.json())

        payload: Dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "temperature": temperature,
            "user": "github-analyzer-api",
            "metadata": {
                "source": "github-analyzer-api",
                "service_version": settings.API_VERSION,
                "operation": operation,
            },
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.litellm_api_key}",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.litellm_api_base.rstrip("/") + "/v1/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()

        raw = resp.json()
        content = self._extract_response_content(raw)
        if not content:
            # Log the full raw shape so we can diagnose tool_calls / empty content issues
            choices = raw.get("choices", [])
            msg = choices[0].get("message", {}) if choices else {}
            logger.warning(
                "_generate_with_litellm_async empty content model=%s finish_reason=%s "
                "has_tool_calls=%s msg_keys=%s",
                chosen_model,
                choices[0].get("finish_reason") if choices else "N/A",
                bool(msg.get("tool_calls")),
                list(msg.keys()),
            )
        return content

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        """Extract a JSON object from LLM output, handling common response wrappers."""
        if not text or not text.strip():
            return ""
        stripped = text.strip()
        # 1. Markdown code fence: ```json ... ```
        fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
        if fence:
            return fence.group(1).strip()
        # 2. JSON object embedded in prose: find outermost { ... }
        obj = re.search(r"\{[\s\S]*\}", stripped)
        if obj:
            return obj.group(0)
        return stripped

    def _extract_response_content(self, response: Any) -> str:
        if response is None:
            logger.warning("_extract_response_content received None")
            return ""

        # SDK object with .choices (openai-sdk style)
        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice, "message"):
                content = getattr(choice.message, "content", None)
                if content is not None:
                    return str(content)
            if hasattr(choice, "text"):
                return getattr(choice, "text", "")

        # SDK object with .message
        if hasattr(response, "message"):
            message = response.message
            return getattr(message, "content", getattr(message, "text", ""))

        # Plain dict (httpx resp.json())
        if isinstance(response, dict):
            # Anthropic native top-level content array
            top_content = response.get("content")
            if isinstance(top_content, list):
                for block in top_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
            elif top_content:
                return str(top_content)

            # OpenAI-compatible choices array
            choices = response.get("choices")
            if choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message") or {}
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if content:  # non-None and non-empty
                            return str(content)
                        # content is None or "" — LiteLLM may have used tool_calls internally
                        # to enforce JSON (happens when response_format is sent to Claude).
                        # Extract the JSON from tool_calls[0].function.arguments.
                        tool_calls = msg.get("tool_calls") or []
                        if tool_calls:
                            try:
                                args = tool_calls[0]["function"]["arguments"]
                                if args:
                                    logger.debug(
                                        "_extract_response_content: extracted %d chars from tool_calls",
                                        len(args),
                                    )
                                    return str(args)
                            except (KeyError, TypeError, IndexError):
                                pass
                        logger.warning(
                            "_extract_response_content: content is None/empty and no tool_calls, "
                            "finish_reason=%s msg_keys=%s",
                            first.get("finish_reason"),
                            list(msg.keys()),
                        )
                        return ""
            logger.warning(
                "_extract_response_content: unrecognised dict shape, keys=%s",
                list(response.keys()),
            )
            return ""

        if hasattr(response, "text"):
            return getattr(response, "text", "")

        return str(response)

    # ==================== Helper Methods ====================

    @staticmethod
    def _build_chat_system_prompt(context: Optional[Dict[str, Any]]) -> str:
        """Build a rich system prompt for the chat endpoint including full metrics context."""
        lines = [
            "You are an expert GitHub engineering analytics assistant. "
            "Answer questions about the repository data provided. "
            "Be specific and cite numbers from the context. "
            "If a question cannot be answered from the data provided, say so clearly."
        ]

        if context and "metrics" in context:
            m = context["metrics"]
            top_contributors = m.get("top_contributors", [])
            top_reviewers = m.get("top_reviewers", [])

            lines += [
                "",
                f"REPOSITORY: {m.get('owner')}/{m.get('repo')}",
                f"PERIOD: {m.get('analysis_period')}",
                "",
                "THROUGHPUT",
                f"- PRs merged: {m.get('total_prs_merged', 0)}",
                f"- Issues closed: {m.get('total_issues_closed', 0)}",
                f"- Reviews submitted: {m.get('total_reviews', 0)}",
                "",
                "SPEED",
                f"- Avg cycle time: {m.get('avg_cycle_time_hours', 0):.1f} h  (median: {m.get('median_cycle_time_hours', 0):.1f} h)",
                f"- Avg review latency: {m.get('avg_review_latency_hours', 0):.1f} h  (median: {m.get('median_review_latency_hours', 0):.1f} h)",
                f"- Velocity trend: {m.get('velocity_trend', 'unknown')}",
                f"- Quality score: {m.get('quality_score', 0):.2f} / 1.00",
                "",
                "TEAM",
                f"- Unique contributors: {m.get('unique_contributors', 0)}",
                f"- Unique reviewers: {m.get('unique_reviewers', 0)}",
            ]

            if top_contributors:
                lines.append("")
                lines.append("TOP CONTRIBUTORS")
                for eng in top_contributors[:5]:
                    lines.append(
                        f"  {eng.get('username')}: {eng.get('prs_merged', 0)} PRs, "
                        f"{eng.get('reviews_completed', 0)} reviews, "
                        f"score {eng.get('contribution_score', 0):.2f}"
                    )

            if top_reviewers:
                lines.append("")
                lines.append("TOP REVIEWERS")
                for eng in top_reviewers[:5]:
                    lines.append(
                        f"  {eng.get('username')}: {eng.get('reviews_completed', 0)} reviews, "
                        f"{eng.get('prs_merged', 0)} PRs, "
                        f"score {eng.get('contribution_score', 0):.2f}"
                    )

        return "\n".join(lines)

    def _build_summary_prompt(self, metrics: RepositoryMetrics) -> str:
        """Build prompt for metrics summary."""
        review_coverage = (
            f"{metrics.total_reviews / metrics.total_prs_merged:.1f} reviews/PR"
            if metrics.total_prs_merged else "N/A"
        )
        cycle_benchmark = (
            "below industry average (good)" if metrics.avg_cycle_time_hours < 24
            else "above industry average (needs attention)" if metrics.avg_cycle_time_hours > 72
            else "within industry average"
        )
        latency_benchmark = (
            "fast (< 4 h — good)" if metrics.avg_review_latency_hours < 4
            else "slow (> 24 h — bottleneck risk)" if metrics.avg_review_latency_hours > 24
            else "moderate"
        )

        return f"""Analyze the following GitHub engineering metrics for {metrics.owner}/{metrics.repo} \
and return a JSON object with actionable insights.

PERIOD: {metrics.analysis_period}

THROUGHPUT
- PRs merged: {metrics.total_prs_merged}
- Issues closed: {metrics.total_issues_closed}
- Reviews submitted: {metrics.total_reviews}
- Review coverage: {review_coverage}

SPEED
- Avg cycle time: {metrics.avg_cycle_time_hours:.1f} h  (median: {metrics.median_cycle_time_hours:.1f} h) — {cycle_benchmark}
- Avg review latency: {metrics.avg_review_latency_hours:.1f} h  (median: {metrics.median_review_latency_hours:.1f} h) — {latency_benchmark}
- Velocity trend: {metrics.velocity_trend}
- Quality score: {metrics.quality_score:.2f} / 1.00

TEAM
- Unique contributors: {metrics.unique_contributors}
- Unique reviewers: {metrics.unique_reviewers}

TOP CONTRIBUTORS (by contribution score)
{self._format_engineers(metrics.top_contributors)}

TOP REVIEWERS (by reviews completed)
{self._format_engineers(metrics.top_reviewers)}

Return exactly this JSON structure with all keys populated using real analysis — not placeholder text:
{{
  "summary": "2-3 sentence executive summary of engineering health and velocity",
  "key_findings": [
    "Specific data-backed finding 1",
    "Specific data-backed finding 2",
    "Specific data-backed finding 3"
  ],
  "performance_insights": {{
    "cycle_time": "Interpretation of {metrics.avg_cycle_time_hours:.1f} h avg vs {metrics.median_cycle_time_hours:.1f} h median and what the gap implies",
    "review_process": "Interpretation of {metrics.avg_review_latency_hours:.1f} h latency and {review_coverage} review coverage",
    "team_dynamics": "Observation about contributor/reviewer distribution and collaboration patterns"
  }},
  "root_cause_hypotheses": [
    "Hypothesis explaining the most significant metric pattern",
    "Second hypothesis if applicable"
  ],
  "recommendations": [
    "Concrete, prioritised action item 1",
    "Concrete, prioritised action item 2",
    "Concrete, prioritised action item 3"
  ],
  "confidence_score": 0.0
}}

Set confidence_score between 0.0 and 1.0 based on how complete the data is \
(penalise for small sample sizes or missing reviewers)."""

    @staticmethod
    def _format_engineers(engineers: list) -> str:
        """Format engineer metrics for display"""
        if not engineers:
            return "No data available"

        lines = []
        for eng in engineers[:5]:  # Top 5
            lines.append(
                f"- {eng.username}: "
                f"{eng.prs_merged} PRs, "
                f"{eng.reviews_completed} reviews, "
                f"Score: {eng.contribution_score:.2f}"
            )
        return "\n".join(lines)
