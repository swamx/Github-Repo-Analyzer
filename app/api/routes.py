import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    AnalyzeRequest, AnalyzeResponse, ChatRequest, ChatResponse, ErrorResponse,
)
from app.services.github_service import GitHubService
from app.services.analytics_service import AnalyticsService
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

router = APIRouter()

github_service = GitHubService()
analytics_service = AnalyticsService()
llm_service = LLMService()


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Analytics"],
    summary="Analyze GitHub Repository",
    description="Fetch GitHub repository data and generate engineering metrics with LLM analysis",
)
async def analyze_repository(request: AnalyzeRequest) -> AnalyzeResponse:
    logger.info("analyze_repository repo_url=%s", request.repo_url)
    try:
        owner, repo = github_service._parse_repo_url(request.repo_url)
        github_data = github_service.fetch_repository_data(
            owner=owner, repo=repo,
            start_time=request.start_time, end_time=request.end_time,
        )
        metrics = analytics_service.generate_metrics(
            github_data=github_data, owner=owner, repo=repo,
            start_time=request.start_time, end_time=request.end_time,
        )
        analysis = await llm_service.summarize_metrics_async(metrics)
        logger.info("analyze_repository completed owner=%s repo=%s", owner, repo)
        return AnalyzeResponse(metrics=metrics, analysis=analysis)

    except ValueError as e:
        logger.warning("analyze_repository bad request: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("analyze_repository runtime error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("analyze_repository unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get(
    "/metrics",
    response_model=dict,
    tags=["Analytics"],
    summary="Get Repository Metrics",
    description="Fetch just the metrics without LLM analysis",
)
async def get_metrics(
    repo_url: str = Query(..., description="GitHub repository URL"),
    start_time: Optional[datetime] = Query(None, description="Start time (ISO format)"),
    end_time: Optional[datetime] = Query(None, description="End time (ISO format)"),
) -> dict:
    logger.info("get_metrics repo_url=%s", repo_url)
    try:
        owner, repo = github_service._parse_repo_url(repo_url)
        github_data = github_service.fetch_repository_data(owner, repo, start_time, end_time)
        metrics = analytics_service.generate_metrics(github_data, owner, repo, start_time, end_time)
        logger.info("get_metrics completed owner=%s repo=%s", owner, repo)
        return metrics.model_dump()
    except ValueError as e:
        logger.warning("get_metrics bad request: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("get_metrics runtime error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("get_metrics unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.post(
    "/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    summary="Chat with Analytics Agent",
    description="Interact with the LLM for exploratory repository analysis",
)
async def chat(request: ChatRequest) -> ChatResponse:
    logger.info("chat message_length=%d repo_url=%s", len(request.message), request.repo_url)
    try:
        context = {}

        if request.repo_url:
            owner, repo = github_service._parse_repo_url(request.repo_url)
            github_data = github_service.fetch_repository_data(
                owner=owner, repo=repo,
                start_time=request.start_time, end_time=request.end_time,
            )
            metrics = analytics_service.generate_metrics(
                github_data=github_data, owner=owner, repo=repo,
                start_time=request.start_time, end_time=request.end_time,
            )
            context["metrics"] = metrics.model_dump()

        history = None
        if request.conversation_history:
            history = [msg.model_dump() for msg in request.conversation_history]

        response_text = await llm_service.chat_message_async(
            message=request.message,
            context=context,
            conversation_history=history,
        )

        turn_number = len(request.conversation_history) + 1 if request.conversation_history else 1
        logger.info("chat completed turn=%d", turn_number)
        return ChatResponse(
            message=response_text,
            context=context if context else None,
            tool_calls=None,
            conversation_turn=turn_number,
        )

    except ValueError as e:
        logger.warning("chat bad request: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("chat runtime error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("chat unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


async def _check_litellm() -> dict:
    """Ping the LiteLLM proxy /health endpoint and return a structured result."""
    url = llm_service.litellm_api_base.rstrip("/") + "/health"
    headers = {"Authorization": f"Bearer {llm_service.litellm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return {"status": "healthy", "http_status": resp.status_code, "detail": resp.json()}
        return {"status": "degraded", "http_status": resp.status_code, "detail": resp.text}
    except httpx.ConnectError as e:
        logger.warning("LiteLLM health check connect error: %s", e)
        return {"status": "unreachable", "error": "connection refused"}
    except httpx.TimeoutException:
        logger.warning("LiteLLM health check timed out")
        return {"status": "unreachable", "error": "timeout"}
    except Exception as e:
        logger.warning("LiteLLM health check failed: %s", e)
        return {"status": "unreachable", "error": str(e)}


@router.get("/health", tags=["System"], summary="Health Check")
async def health_check() -> dict:
    cb_state = llm_service.litellm_circuit_breaker.get_state()
    litellm_health = await _check_litellm()

    circuit_open = cb_state["state"] == "open"
    litellm_ok = litellm_health["status"] == "healthy"

    llm_status = "healthy" if (litellm_ok and not circuit_open) else "degraded"
    overall = "healthy" if llm_status == "healthy" else "degraded"

    return {
        "status": overall,
        "service": "github-engineering-intelligence",
        "version": "2.0.0",
        "components": {
            "llm": {
                "status": llm_status,
                "model": llm_service.primary_model,
                "circuit_breaker": cb_state,
                "litellm": litellm_health,
            },
        },
    }
