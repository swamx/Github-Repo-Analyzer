"""LLM-as-a-Judge service for blind comparison of prompt variants."""
import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.models.judge_schemas import (
    CriterionScore,
    JudgeRequest,
    JudgeResponse,
    PairwiseComparison,
    PromptVariant,
    VariantResult,
)

logger = logging.getLogger(__name__)

_LABELS = "ABCDEFGHIJ"

_TRACKING_METADATA = {
    "source": "github-analyzer-api",
    "service_version": settings.API_VERSION,
    "component": "judge",
}


class JudgeService:
    """
    Runs prompt variants concurrently, then asks an LLM judge to score
    each output blind (using neutral labels A/B/C) across the requested
    criteria and produce pairwise comparisons.
    """

    def __init__(self) -> None:
        self._api_base = settings.LITELLM_API_BASE.rstrip("/")
        self._api_key = settings.LITELLM_API_KEY
        self._primary_model = settings.PRIMARY_MODEL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compare(self, request: JudgeRequest) -> JudgeResponse:
        logger.info(
            "judge.compare variants=%s runs_per_variant=%d judge_model=%s",
            [v.name for v in request.variants],
            request.runs_per_variant,
            request.judge_model or self._primary_model,
        )

        # tasks are ordered: variant[0]*runs, variant[1]*runs, ...
        run_tasks = [
            self._run_variant(variant, request.input)
            for variant in request.variants
            for _ in range(request.runs_per_variant)
        ]
        raw_runs: list[dict | Exception] = list(
            await asyncio.gather(*run_tasks, return_exceptions=True)
        )

        aggregated = self._aggregate_runs(request.variants, raw_runs, request.runs_per_variant)
        if not aggregated:
            raise RuntimeError("All variant runs failed — nothing to judge")

        logger.info("judge.compare all variants executed, sending to judge")

        judge_result = await self._judge(
            input_text=request.input,
            aggregated=aggregated,
            criteria=request.criteria,
            judge_model=request.judge_model or self._primary_model,
        )

        logger.info("judge.compare winner=%s", judge_result.winner)
        return judge_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_variant(self, variant: PromptVariant, input_text: str) -> dict[str, Any]:
        """Call the LiteLLM proxy with one variant's prompt + parameters."""
        user_content = variant.user_prompt.replace("{input}", input_text)
        messages = [
            {"role": "system", "content": variant.system_prompt},
            {"role": "user", "content": user_content},
        ]
        payload = {
            "model": variant.model or self._primary_model,
            "messages": messages,
            "temperature": variant.temperature,
            "user": "github-analyzer-api",
            "metadata": {**_TRACKING_METADATA, "operation": "judge_variant", "variant": variant.name},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        url = f"{self._api_base}/v1/chat/completions"

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("variant '%s' run failed: %s", variant.name, exc)
            raise

        latency_ms = (time.monotonic() - t0) * 1000
        output = resp.json()["choices"][0]["message"]["content"] or ""
        logger.debug("variant '%s' completed latency=%.0fms output_len=%d", variant.name, latency_ms, len(output))
        return {"name": variant.name, "description": variant.description, "output": output, "latency_ms": latency_ms}

    @staticmethod
    def _aggregate_runs(
        variants: list[PromptVariant],
        raw_runs: list[dict | Exception],
        runs_per_variant: int,
    ) -> dict[str, dict[str, Any]]:
        """
        Group runs by variant. Tasks are ordered variant[0]*N, variant[1]*N, ...,
        so variant index = i // runs_per_variant.
        Average latency; keep last successful output.
        """
        buckets: dict[str, list[dict]] = {v.name: [] for v in variants}
        for i, result in enumerate(raw_runs):
            if isinstance(result, Exception):
                continue
            variant_index = i // runs_per_variant
            variant_name = variants[variant_index].name
            buckets[variant_name].append(result)

        aggregated: dict[str, dict[str, Any]] = {}
        for name, runs in buckets.items():
            if not runs:
                logger.warning("variant '%s' had no successful runs, skipping", name)
                continue
            aggregated[name] = {
                "name": name,
                "description": runs[-1].get("description"),
                "output": runs[-1]["output"],
                "latency_ms": sum(r["latency_ms"] for r in runs) / len(runs),
            }
        return aggregated

    async def _judge(
        self,
        input_text: str,
        aggregated: dict[str, dict[str, Any]],
        criteria: list[str],
        judge_model: str,
    ) -> JudgeResponse:
        """Ask the judge LLM to score and compare all variant outputs."""

        names = list(aggregated.keys())
        label_to_name = {_LABELS[i]: names[i] for i in range(len(names))}
        name_to_label = {v: k for k, v in label_to_name.items()}

        formatted_outputs = "\n\n".join(
            f"=== Output {name_to_label[name]} ===\n{data['output']}"
            for name, data in aggregated.items()
        )
        criteria_list = ", ".join(criteria)

        score_template = {
            f"Output {lbl}": {c: 0.0 for c in criteria} | {"total": 0.0, "notes": ""}
            for lbl in name_to_label.values()
        }
        pairwise_pairs = [
            {
                "a": f"Output {name_to_label[names[i]]}",
                "b": f"Output {name_to_label[names[j]]}",
                "preferred": f"Output {name_to_label[names[i]]}",
                "reasoning": "",
            }
            for i in range(len(names)) for j in range(i + 1, len(names))
        ]

        first_label = f'"Output {name_to_label[names[0]]}"'

        judge_prompt = f"""You are an expert AI evaluator. Perform a blind evaluation of the outputs below.

INPUT GIVEN TO EACH MODEL:
{input_text}

OUTPUTS:
{formatted_outputs}

EVALUATION CRITERIA: {criteria_list}

Instructions:
1. Score each output independently on each criterion from 0.0 (poor) to 1.0 (excellent).
2. Set "total" to the unweighted average of all criterion scores for that output.
3. For each pair, choose the preferred output and give a one-sentence reason.
4. Set "winner" to the label of the highest-scoring output.

Return ONLY a valid JSON object with this structure (replace placeholder values with real analysis):
{{
  "scores": {json.dumps(score_template, indent=2)},
  "winner": {first_label},
  "winner_reasoning": "one sentence explaining why this output won",
  "pairwise": {json.dumps(pairwise_pairs, indent=2)}
}}"""

        payload = {
            "model": judge_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert AI evaluator. "
                        "Respond with ONLY a valid JSON object — no markdown fences, no prose."
                    ),
                },
                {"role": "user", "content": judge_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "user": "github-analyzer-api",
            "metadata": {**_TRACKING_METADATA, "operation": "judge_eval"},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self._api_base}/v1/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()

        raw = resp.json()["choices"][0]["message"]["content"] or ""
        logger.debug("judge raw response length=%d", len(raw))

        # Strip any markdown fences the model may still add
        raw = self._strip_fences(raw)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("judge returned invalid JSON (first 500 chars): %r", raw[:500])
            raise RuntimeError(f"Judge returned invalid JSON: {exc}") from exc

        return self._build_response(parsed, aggregated, name_to_label, label_to_name, criteria)

    @staticmethod
    def _strip_fences(text: str) -> str:
        if not text or not text.strip():
            return ""
        stripped = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
        if fence:
            return fence.group(1).strip()
        obj = re.search(r"\{[\s\S]*\}", stripped)
        if obj:
            return obj.group(0)
        return stripped

    @staticmethod
    def _build_response(
        parsed: dict,
        aggregated: dict[str, dict[str, Any]],
        name_to_label: dict[str, str],
        label_to_name: dict[str, str],
        criteria: list[str],
    ) -> JudgeResponse:
        scores_raw: dict = parsed.get("scores", {})

        def resolve_label(raw_label: str) -> str:
            lbl = str(raw_label).replace("Output ", "").strip()
            return label_to_name.get(lbl, raw_label)

        variant_results: list[VariantResult] = []
        for name, data in aggregated.items():
            lbl = name_to_label[name]
            key = f"Output {lbl}"
            raw_scores = scores_raw.get(key, {})

            criterion_scores = {
                c: CriterionScore(score=float(raw_scores.get(c, 0.0)))
                for c in criteria
            }
            total = float(raw_scores.get("total", 0.0))
            if total == 0.0 and criterion_scores:
                total = sum(cs.score for cs in criterion_scores.values()) / len(criterion_scores)

            variant_results.append(VariantResult(
                name=name,
                description=data.get("description"),
                output=data["output"],
                latency_ms=round(data["latency_ms"], 1),
                criterion_scores=criterion_scores,
                total_score=round(total, 4),
                judge_notes=str(raw_scores.get("notes", "")),
            ))

        winner_name = resolve_label(str(parsed.get("winner", "")))
        if winner_name not in aggregated:
            # fall back to highest scoring variant
            winner_name = max(variant_results, key=lambda v: v.total_score).name

        pairwise: list[PairwiseComparison] = [
            PairwiseComparison(
                variant_a=resolve_label(p.get("a", "")),
                variant_b=resolve_label(p.get("b", "")),
                preferred=resolve_label(p.get("preferred", "")),
                reasoning=str(p.get("reasoning", "")),
            )
            for p in parsed.get("pairwise", [])
        ]

        return JudgeResponse(
            winner=winner_name,
            variants=variant_results,
            judge_reasoning=str(parsed.get("winner_reasoning", "")),
            pairwise_comparisons=pairwise,
        )
