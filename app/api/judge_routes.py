import logging

from fastapi import APIRouter, HTTPException

from app.models.judge_schemas import JudgeRequest, JudgeResponse, JudgeErrorResponse
from app.services.judge_service import JudgeService

logger = logging.getLogger(__name__)

router = APIRouter()
_judge_service = JudgeService()


@router.post(
    "/compare",
    response_model=JudgeResponse,
    responses={
        400: {"model": JudgeErrorResponse},
        500: {"model": JudgeErrorResponse},
    },
    summary="Compare prompt variants",
    description=(
        "Run two or more prompt variants against the same input concurrently, "
        "then have an LLM judge score each output blind across the requested "
        "criteria and produce pairwise comparisons. "
        "Use this to optimize prompts, temperatures, or model choice."
    ),
)
async def compare_variants(request: JudgeRequest) -> JudgeResponse:
    """
    **Blind LLM-as-a-Judge evaluation.**

    Each variant runs with its own `system_prompt`, `user_prompt`, `model`,
    and `temperature`. The judge sees outputs labelled A/B/C (no variant
    names) to prevent label bias, then scores them on each criterion and
    picks a winner.

    **Minimal example** — two system prompts, same model:
    ```json
    {
      "input": "Who are the top contributors to microsoft/vscode?",
      "variants": [
        {
          "name": "baseline",
          "system_prompt": "You are a helpful assistant.",
          "user_prompt": "{input}"
        },
        {
          "name": "expert",
          "system_prompt": "You are an expert software engineering metrics analyst. Be concise and data-driven.",
          "user_prompt": "{input}"
        }
      ]
    }
    ```
    """
    logger.info(
        "POST /judge/compare variants=%s input_length=%d",
        [v.name for v in request.variants],
        len(request.input),
    )

    if len(request.variants) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 variants per request")
    if len(request.input) > 10_000:
        raise HTTPException(status_code=400, detail="Input must be ≤ 10 000 characters")

    try:
        result = await _judge_service.compare(request)
        logger.info("POST /judge/compare completed winner=%s", result.winner)
        return result
    except ValueError as exc:
        logger.warning("judge compare bad request: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.error("judge compare runtime error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("judge compare unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
