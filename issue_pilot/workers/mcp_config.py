"""Build the lean-ctx 3.0 MCP config file that is passed to each `claude` subprocess.

lean-ctx is already installed in this environment (see CLAUDE.md).  We write a
per-worker temp JSON file so every subprocess gets a clean MCP connection.

lean-ctx 3.0 tools used by workers:
  ctx_overview   — one-shot project map (replaces manual ls + cat at session start)
  ctx_read       — cached compressed file reads (10 modes, ~13 tok on re-reads)
  ctx_search     — ripgrep wrapper with compact output
  ctx_shell      — bash with 95+ compression patterns
  ctx_tree       — directory listing
  ctx_knowledge  — remember/recall skills and session context
"""

from __future__ import annotations

import json
import os
import tempfile

from issue_pilot.config import settings

# lean-ctx server launch command — matches the environment setup in CLAUDE.md.
# Adjust LEAN_CTX_CMD in .env if your install differs.
_LEAN_CTX_CMD: list[str] = settings.LEAN_CTX_CMD.split()


def write_mcp_config(workdir: str) -> str:
    """Write a lean-ctx MCP config JSON for one worker subprocess.

    Returns the path to the temp file (caller must delete it after the
    subprocess exits).
    """
    config = {
        "mcpServers": {
            "lean-ctx": {
                "command": _LEAN_CTX_CMD[0],
                "args": _LEAN_CTX_CMD[1:],
                "env": {
                    # lean-ctx 3.0: set the working dir so ctx_tree/ctx_read
                    # resolve relative paths against the cloned repo.
                    "LEAN_CTX_WORKDIR": workdir,
                },
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="issue_pilot_mcp_")
    with os.fdopen(fd, "w") as fh:
        json.dump(config, fh, indent=2)
    return path


# Tools we allow the Claude Code worker to use.
# Restricting to lean-ctx + file-editing tools keeps the agent focused
# and prevents accidental network calls outside GitHub.
ALLOWED_TOOLS = ",".join([
    "mcp__lean-ctx__ctx_overview",
    "mcp__lean-ctx__ctx_read",
    "mcp__lean-ctx__ctx_search",
    "mcp__lean-ctx__ctx_shell",
    "mcp__lean-ctx__ctx_tree",
    "mcp__lean-ctx__ctx_knowledge",
    "mcp__lean-ctx__ctx_session",
    "Edit",
    "Write",
    "Bash",
])
