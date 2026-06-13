"""Sandboxed tools. Every path is forced to live inside config.WORKSPACE.

These are plain Python functions, not LangChain tools, because our nodes call
them directly. (Once you're comfortable, swap these for @tool functions or an
MCP server such as Playwright — the graph doesn't change, only this file does.)
"""
import subprocess
from pathlib import Path
from typing import Optional

from config import WORKSPACE


def _safe(path: str) -> Path:
    """Resolve a workspace-relative path, refusing anything that escapes the sandbox."""
    p = (WORKSPACE / path).resolve()
    if not str(p).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path '{path}' escapes the workspace sandbox.")
    return p


def list_dir() -> str:
    """A simple tree of the workspace so the planner knows what's there."""
    root = WORKSPACE.resolve()
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        depth = len(rel.parts) - 1
        lines.append("  " * depth + ("📁 " if p.is_dir() else "📄 ") + rel.name)
    return "\n".join(lines) if lines else "(empty workspace)"


def read_file(path: str) -> str:
    p = _safe(path)
    if not p.exists():
        return f"[file not found: {path}]"
    return p.read_text(encoding="utf-8", errors="replace")


def read_text_if_exists(path: str) -> Optional[str]:
    """Return the file's contents, or None if it doesn't exist (no sentinel string).
    Used by the change-aware write to compare against what's already on disk."""
    p = _safe(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def write_file(path: str, content: str) -> str:
    """SIDE-EFFECTING. Only called from the execute node, after human approval."""
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


# Commands that fetch from the network. We block these so the agent can't bypass
# the browser node — web access must go through the 'browse' action (Playwright/MCP),
# which renders JS. This enforces the architecture rather than trusting the prompt alone.
_WEB_FETCH_TOKENS = ("curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod",
                     "requests.get", "urllib", "httpx", "system.net.webclient")


def run_command(command: str) -> str:
    """SIDE-EFFECTING. Runs inside the workspace dir. Approval-gated."""
    low = command.lower()
    if any(tok in low for tok in _WEB_FETCH_TOKENS):
        return ("[blocked: this command fetches from the web. Use the 'browse' action with "
                "the url instead — web access goes through the Playwright/MCP browser, which "
                "renders JavaScript. Shell-based fetching is disabled here.]")
    try:
        proc = subprocess.run(
            command, shell=True, cwd=WORKSPACE, capture_output=True,
            text=True, timeout=60,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"exit={proc.returncode}\n{out.strip()}"
    except subprocess.TimeoutExpired:
        return "[command timed out after 60s]"
