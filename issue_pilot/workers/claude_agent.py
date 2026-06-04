"""Launch one `claude` CLI subprocess per GitHub issue.

Each subprocess is a full Claude Code agent with:
  • lean-ctx 3.0 MCP server attached (ctx_read / ctx_search / ctx_shell / …)
  • Skill-aware prompt (tech-stack detected from the cloned repo)
  • Restricted tool set — only lean-ctx + Edit/Write/Bash

The subprocess is async so the Google ADK coordinator can fan out all issues
in parallel via asyncio.gather without blocking the event loop.

Return value: WorkerResult dict that gets stored back to Redis by the coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Any

from issue_pilot.config import settings
from issue_pilot.workers.mcp_config import ALLOWED_TOOLS, write_mcp_config
from issue_pilot.workers.skills import detect_skills, skill_load_instruction

logger = logging.getLogger(__name__)

# ── prompt template ───────────────────────────────────────────────────────────

_WORKER_PROMPT = """\
You are an autonomous software engineer fixing GitHub issue #{issue_number} in {repo_url}.

## Context loading (do this FIRST — reduces token usage via lean-ctx 3.0)
1. Call ctx_overview to get a compressed project map.
2. {skill_instruction}
3. Call ctx_search to find code related to the issue.
4. Call ctx_read (mode=map or signatures) on relevant files — NOT full mode unless editing.

## Your task
Issue #{issue_number}: {issue_title}

{issue_body}

{issue_comments_section}

## Requirements
- Research the root cause using ctx_search and ctx_read before touching any file.
- Make the minimal correct fix. Do NOT refactor unrelated code.
- Write or update tests if applicable.
- After editing, run tests with ctx_shell (e.g. `pytest -x -q`) and confirm they pass.
- Commit your changes: `git add -A && git commit -m "fix: #{issue_number} <short description>"`

## Token efficiency rules (lean-ctx 3.0)
- Use ctx_read with mode=map or signatures for context; full only when editing.
- Use ctx_search instead of reading whole directories.
- Use ctx_shell for one-shot commands (compilation, tests, lint).
- Never cat large files raw — always go through ctx_read.

## Output (last message)
Return a JSON object:
{{
  "issue_number": {issue_number},
  "title": "{issue_title}",
  "plan": "<markdown fix plan>",
  "files_changed": ["path/to/file", ...],
  "tests_passed": true | false,
  "commit_sha": "<sha or empty>"
}}
"""


def _build_prompt(
    repo_url: str,
    issue_number: int,
    issue_data: dict[str, Any],
    repo_path: str,
) -> str:
    skills = detect_skills(repo_path)
    skill_instr = skill_load_instruction(skills)

    comments = issue_data.get("comments", [])
    comments_section = ""
    if comments:
        formatted = "\n".join(f"- {c}" for c in comments[:10])
        comments_section = f"## Issue comments\n{formatted}"

    return _WORKER_PROMPT.format(
        issue_number=issue_number,
        repo_url=repo_url,
        issue_title=issue_data.get("title", f"Issue #{issue_number}"),
        issue_body=issue_data.get("body", "(no description)"),
        issue_comments_section=comments_section,
        skill_instruction=skill_instr,
    )


# ── repo helpers ──────────────────────────────────────────────────────────────

async def _clone_repo(repo_url: str, branch: str, dest: str) -> None:
    """Shallow-clone repo at the given branch into dest."""
    cmd = ["git", "clone", "--depth", "1", "--branch", branch, repo_url, dest]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Branch may not exist yet — clone default and create branch
        cmd2 = ["git", "clone", "--depth", "1", repo_url, dest]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd2,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()
        # Create the branch locally
        proc3 = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch,
            cwd=dest,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc3.communicate()


# ── main async worker ─────────────────────────────────────────────────────────

async def dispatch_claude_worker(
    job_id: str,
    repo_url: str,
    issue_number: int,
    base_branch: str,
    issue_data: dict[str, Any],
    branch_name: str,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Clone the repo, launch a `claude` subprocess for one issue, return result dict."""

    workdir = tempfile.mkdtemp(prefix=f"issue_pilot_{job_id}_{issue_number}_")
    mcp_cfg_path: str | None = None

    try:
        # 1. Clone
        logger.info("[%d] Cloning %s → %s", issue_number, repo_url, workdir)
        await _clone_repo(repo_url, branch_name, workdir)

        # 2. Build prompt
        prompt = _build_prompt(repo_url, issue_number, issue_data, workdir)

        # 3. Write lean-ctx MCP config
        mcp_cfg_path = write_mcp_config(workdir)

        # 4. Launch `claude` CLI
        env = os.environ.copy()
        # Ensure ANTHROPIC_API_KEY is visible to the subprocess
        if settings.ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY

        cmd = [
            "claude",
            "--mcp-config", mcp_cfg_path,
            "--allowedTools", ALLOWED_TOOLS,
            "--output-format", "json",
            "-p", prompt,
        ]

        logger.info("[%d] Launching claude agent (timeout=%ds)", issue_number, timeout_s)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("[%d] Claude agent timed out after %ds", issue_number, timeout_s)
            return {
                "issue_number": issue_number,
                "title": issue_data.get("title", f"Issue #{issue_number}"),
                "plan": "",
                "files_changed": [],
                "tests_passed": False,
                "commit_sha": "",
                "error": f"Timed out after {timeout_s}s",
            }

        # 5. Parse JSON output from the claude CLI
        result = _parse_claude_output(stdout.decode(errors="replace"), issue_number, issue_data)

        if proc.returncode != 0:
            logger.warning("[%d] claude exited %d: %s", issue_number, proc.returncode, stderr.decode()[:300])
            result["error"] = stderr.decode(errors="replace")[:500]

        return result

    finally:
        if mcp_cfg_path and os.path.exists(mcp_cfg_path):
            os.unlink(mcp_cfg_path)
        shutil.rmtree(workdir, ignore_errors=True)


def _parse_claude_output(raw: str, issue_number: int, issue_data: dict[str, Any]) -> dict[str, Any]:
    """Extract the JSON result from the claude CLI's streaming output."""
    # claude --output-format json emits a stream of JSON objects, one per message.
    # The last assistant message with a JSON body is our structured result.
    fallback = {
        "issue_number": issue_number,
        "title": issue_data.get("title", f"Issue #{issue_number}"),
        "plan": raw[:2000] if raw else "[no output]",
        "files_changed": [],
        "tests_passed": False,
        "commit_sha": "",
    }

    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            # claude --output-format json wraps each turn in {"type": "...", "message": {...}}
            if isinstance(obj, dict):
                msg = obj.get("message", obj)
                content = msg.get("content", [])
                for block in reversed(content if isinstance(content, list) else []):
                    text = block.get("text", "") if isinstance(block, dict) else ""
                    # Look for our structured JSON payload inside the text
                    start = text.rfind("{")
                    end = text.rfind("}") + 1
                    if start >= 0 and end > start:
                        try:
                            candidate = json.loads(text[start:end])
                            if "issue_number" in candidate:
                                return candidate
                        except json.JSONDecodeError:
                            pass
        except (json.JSONDecodeError, AttributeError):
            continue

    return fallback
