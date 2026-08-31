"""Deterministic review orchestration (doc section 6 — no LLM)."""
import logging
from typing import Optional

from app.services.code_review.schemas import RiskProfile, RiskLevel

logger = logging.getLogger(__name__)


class ReviewOrchestrator:
    """Decides which agents to run based on risk profiles (deterministic, no LLM)."""

    def __init__(self):
        pass

    def plan_review(self, risk_profiles: dict[str, RiskProfile]) -> dict[str, list[str]]:
        """Determine which agents should run on which files (doc section 6).

        Args:
            risk_profiles: {file_path: RiskProfile}

        Returns:
            Mapping of file → list of agent names to run:
            {
                "path/to/file.py": ["correctness", "security"],
                "path/to/other.py": ["testing"],
            }
        """
        file_agents = {}

        for file_path, profile in risk_profiles.items():
            agents = []

            # High-risk files → all agents
            if profile.level == RiskLevel.HIGH:
                agents.extend(["correctness", "security", "testing"])
            # Medium-risk → correctness + security
            elif profile.level == RiskLevel.MEDIUM:
                agents.extend(["correctness", "security"])
            # Low-risk → minimal (just correctness)
            else:
                agents.append("correctness")

            # Skip test files (they have correctness, but not security)
            if "test" in file_path.lower() or "spec" in file_path.lower():
                agents = [a for a in agents if a != "security"]

            file_agents[file_path] = agents
            logger.debug(
                "File %s (risk=%s) → agents=%s",
                file_path,
                profile.level,
                agents,
            )

        return file_agents

    def build_context_for_agents(
        self,
        file_path: str,
        file_agents: list[str],
        context: dict,
    ) -> dict[str, dict]:
        """Build prompt context per agent for this file.

        Args:
            file_path: File being reviewed
            file_agents: Agents running on this file
            context: Retrieved context bundle {file_path: {related_files: [...], patch: ...}}

        Returns:
            {agent_name: {patch, related_files, related_content}}
        """
        agent_contexts = {}
        file_context = context.get(file_path, {})

        for agent in file_agents:
            agent_contexts[agent] = {
                "file": file_path,
                "patch": file_context.get("patch", ""),
                "related_files": file_context.get("related_files", []),
                "related_content": file_context.get("related_content", {}),
            }

        return agent_contexts
