"""Detect the repo's tech stack and return a lean-ctx skill-load instruction.

The returned string is injected into the Claude Code worker prompt so the agent
calls ctx_knowledge to load the right skills before touching any code.

Skills understood by lean-ctx 3.0 ctx_knowledge:
  python, fastapi, django, flask, typescript, react, nextjs, go, rust,
  java, spring, kotlin, docker, kubernetes, github-actions, pytest, jest
"""

from __future__ import annotations

import os

# Map file/dir indicators → skill tag
_INDICATORS: list[tuple[str, str]] = [
    # Python
    ("requirements.txt", "python"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("pytest.ini", "pytest"),
    ("conftest.py", "pytest"),
    # Web frameworks
    ("manage.py", "django"),
    ("app/main.py", "fastapi"),
    ("main.py", "fastapi"),
    # TypeScript / JS
    ("tsconfig.json", "typescript"),
    ("package.json", "typescript"),
    ("next.config.js", "nextjs"),
    ("next.config.ts", "nextjs"),
    # Systems
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    # Infra
    ("Dockerfile", "docker"),
    ("docker-compose.yml", "docker"),
    (".github/workflows", "github-actions"),
    ("k8s", "kubernetes"),
    ("kubernetes", "kubernetes"),
]


def detect_skills(repo_path: str) -> list[str]:
    """Walk the top level of a cloned repo and return matching skill tags."""
    found: list[str] = []
    seen: set[str] = set()
    for indicator, skill in _INDICATORS:
        if skill in seen:
            continue
        full = os.path.join(repo_path, indicator)
        if os.path.exists(full):
            found.append(skill)
            seen.add(skill)
    # Default to generic coding skill if nothing detected
    return found or ["python"]


def skill_load_instruction(skills: list[str]) -> str:
    """Return the ctx_knowledge recall instruction to embed in the worker prompt."""
    if not skills:
        return ""
    tags = ", ".join(f'"{s}"' for s in skills)
    return (
        f"Before reading any code, call ctx_knowledge with action='recall' "
        f"and query={tags} to load the relevant coding skills and best-practice "
        f"context into your session. This reduces token usage on subsequent reads."
    )
