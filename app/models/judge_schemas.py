from typing import Optional
from pydantic import BaseModel, Field


class PromptVariant(BaseModel):
    name: str = Field(..., description="Unique label for this variant, e.g. 'baseline' or 'v2'")
    description: Optional[str] = Field(
        None,
        description="Human-readable explanation of the prompt strategy — shown in results and Swagger docs.",
    )
    system_prompt: str = Field(..., description="System prompt for this variant")
    user_prompt: str = Field(
        ...,
        description="User prompt. Use {input} as placeholder for the test input.",
    )
    model: Optional[str] = Field(
        None,
        description="Model name as defined in LiteLLM config. Defaults to PRIMARY_MODEL.",
    )
    temperature: float = Field(0.2, ge=0.0, le=2.0)


class JudgeRequest(BaseModel):
    input: str = Field(..., description="The test input injected into each variant's user_prompt")
    variants: list[PromptVariant] = Field(..., min_length=2, description="At least two variants to compare")
    judge_model: Optional[str] = Field(
        None,
        description="Model used to judge outputs. Defaults to PRIMARY_MODEL.",
    )
    criteria: list[str] = Field(
        default=["accuracy", "helpfulness", "clarity", "conciseness"],
        description="Evaluation dimensions scored 0–1 by the judge",
    )
    runs_per_variant: int = Field(
        1, ge=1, le=3,
        description="How many times to run each variant. Scores are averaged across runs.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "input": (
                    "Our team merged 42 PRs last month with an average cycle time of 68 hours "
                    "and average review latency of 18 hours. We have 8 contributors and 4 reviewers. "
                    "Top contributor merged 14 PRs."
                ),
                "variants": [
                    {
                        "name": "concise-analyst",
                        "description": (
                            "Low-temperature executive-summary style. "
                            "Forces a 3-bullet structure (good / needs attention / action item). "
                            "Best when the consumer wants a scannable snapshot, not a deep dive."
                        ),
                        "system_prompt": (
                            "You are a senior engineering metrics analyst. "
                            "Give a 3-bullet executive summary: what's good, what needs attention, "
                            "and the single most important action item. Be direct and data-driven."
                        ),
                        "user_prompt": "{input}",
                        "model": "claude-haiku",
                        "temperature": 0.2,
                    },
                    {
                        "name": "detailed-coach",
                        "description": (
                            "Higher-temperature coaching style. "
                            "Explains what each metric means in practical terms, identifies bottlenecks, "
                            "and proposes two specific process improvements with expected impact. "
                            "Best when the team wants to understand *why* and act on it."
                        ),
                        "system_prompt": (
                            "You are an engineering team coach who helps teams improve delivery. "
                            "Analyse the metrics, explain what each number means in practical terms, "
                            "identify bottlenecks, and suggest two specific process improvements "
                            "with expected impact."
                        ),
                        "user_prompt": (
                            "Here are our team's engineering metrics from last month:\n\n"
                            "{input}\n\nWhat should we focus on improving?"
                        ),
                        "model": "claude-haiku",
                        "temperature": 0.4,
                    },
                ],
                "judge_model": "claude-haiku",
                "criteria": ["accuracy", "helpfulness", "clarity", "conciseness"],
                "runs_per_variant": 1,
            }
        }
    }


class CriterionScore(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    notes: str = ""


class VariantResult(BaseModel):
    name: str
    description: Optional[str] = None
    output: str
    latency_ms: float
    criterion_scores: dict[str, CriterionScore]
    total_score: float
    judge_notes: str


class PairwiseComparison(BaseModel):
    variant_a: str
    variant_b: str
    preferred: str
    reasoning: str


class JudgeResponse(BaseModel):
    status: str = "success"
    winner: str
    variants: list[VariantResult]
    judge_reasoning: str
    pairwise_comparisons: list[PairwiseComparison]


class JudgeErrorResponse(BaseModel):
    status: str = "error"
    error: str
    partial_results: Optional[list[VariantResult]] = None
