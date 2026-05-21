"""External LLM wrapper tools for routing heavy Hermes tasks.

These tools wrap Minsu's local Claude Code, Codex, and Gemini helper scripts
under ~/.hermes/bin. They make the routing choice explicit in the tool schema
instead of relying on channel-prompt prose plus a generic terminal call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.registry import registry

_HERMES_BIN = Path.home() / ".hermes" / "bin"
_WRAPPERS = {
    "claude_code_task": _HERMES_BIN / "hermes-claude-code",
    "claude_plan_task": _HERMES_BIN / "hermes-claude-plan",
    "claude_opus_task": _HERMES_BIN / "hermes-claude-opus",
    "claude_review_task": _HERMES_BIN / "hermes-claude-review",
    "codex_web_research": _HERMES_BIN / "hermes-codex-web",
    "codex_consult_task": _HERMES_BIN / "hermes-codex-consult",
    "gemini_scan_task": _HERMES_BIN / "hermes-gemini-consult",
}


def _wrapper_exists(tool_name: str) -> bool:
    path = _WRAPPERS.get(tool_name)
    return bool(path and path.exists() and os.access(path, os.X_OK))


def _check_external_llm_wrappers() -> bool:
    return all(_wrapper_exists(name) for name in _WRAPPERS)


def _check_tool_wrapper(tool_name: str):
    return lambda: _wrapper_exists(tool_name)


def _safe_workdir(value: str | None) -> str | None:
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.exists() or not p.is_dir():
        raise ValueError(f"workdir does not exist or is not a directory: {value}")
    return str(p.resolve())


def _read_preview(path: str | None, limit: int = 4000) -> str:
    if not path:
        return ""
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return data[:limit]


def _extract_file(stdout: str) -> str | None:
    # wrappers print: file: /tmp/...
    m = re.search(r"^file:\s*(\S+)\s*$", stdout, flags=re.MULTILINE)
    if m:
        return m.group(1)
    return None


def _run_wrapper(tool_name: str, args: dict[str, Any], default_timeout: int) -> str:
    prompt = str(args.get("prompt") or args.get("query") or "").strip()
    if not prompt:
        return json.dumps({"success": False, "error": "prompt/query is required"}, ensure_ascii=False)

    wrapper = _WRAPPERS[tool_name]
    if not (wrapper.exists() and os.access(wrapper, os.X_OK)):
        return json.dumps({"success": False, "error": f"wrapper missing or not executable: {wrapper}"}, ensure_ascii=False)

    timeout = int(args.get("timeout_seconds") or default_timeout)
    timeout = max(10, min(timeout, 1800))
    cwd = _safe_workdir(args.get("workdir")) or os.getcwd()

    env = os.environ.copy()
    max_turns = args.get("max_turns")
    if max_turns and tool_name == "claude_code_task":
        try:
            max_turns_int = int(max_turns)
        except (TypeError, ValueError):
            max_turns_int = 80
        env["HERMES_CLAUDE_CODE_MAX_TURNS"] = str(max(1, min(max_turns_int, 80)))

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [str(wrapper), prompt],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.monotonic() - start, 3)
        return json.dumps(
            {
                "success": False,
                "tool": tool_name,
                "wrapper": str(wrapper),
                "workdir": cwd,
                "error": f"wrapper timed out after {timeout}s",
                "stdout": exc.stdout,
                "stderr": exc.stderr,
                "elapsed_seconds": elapsed,
            },
            ensure_ascii=False,
        )

    elapsed = round(time.monotonic() - start, 3)
    output_file = _extract_file(proc.stdout or "")
    result = {
        "success": proc.returncode == 0,
        "tool": tool_name,
        "wrapper": str(wrapper),
        "workdir": cwd,
        "returncode": proc.returncode,
        "output_file": output_file,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
        "preview": _read_preview(output_file),
        "elapsed_seconds": elapsed,
    }
    return json.dumps(result, ensure_ascii=False)


def _schema(name: str, description: str, prompt_name: str = "prompt", default_timeout: int = 300) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                prompt_name: {
                    "type": "string",
                    "description": "Self-contained task prompt/query for the external model wrapper.",
                },
                "workdir": {
                    "type": "string",
                    "description": "Optional absolute project directory. Always set for repo-specific work.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": f"Timeout in seconds, capped at 1800. Default {default_timeout}.",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Claude Code only: cap autonomous turns for implementation work.",
                },
            },
            "required": [prompt_name],
        },
    }


registry.register(
    name="claude_code_task",
    toolset="external_llm",
    schema=_schema(
        "claude_code_task",
        "Route implementation, file edits, refactors, and bug fixes to Claude Code. Use this before doing substantial code edits in the parent loop.",
        default_timeout=900,
    ),
    handler=lambda args, **kw: _run_wrapper("claude_code_task", args, 900),
    check_fn=_check_tool_wrapper("claude_code_task"),
    description="Claude Code implementation wrapper",
    emoji="🛠️",
)

registry.register(
    name="claude_plan_task",
    toolset="external_llm",
    schema=_schema(
        "claude_plan_task",
        "Route architecture, product strategy, implementation planning, and hard tradeoff analysis to Claude Opus plan mode. Read-only; no file edits.",
        default_timeout=600,
    ),
    handler=lambda args, **kw: _run_wrapper("claude_plan_task", args, 600),
    check_fn=_check_tool_wrapper("claude_plan_task"),
    description="Claude planning wrapper",
    emoji="🧠",
)

registry.register(
    name="claude_opus_task",
    toolset="external_llm",
    schema=_schema(
        "claude_opus_task",
        "Route deep reasoning, complex PM/architecture decisions, and synthesis work to Claude Opus max-effort.",
        default_timeout=900,
    ),
    handler=lambda args, **kw: _run_wrapper("claude_opus_task", args, 900),
    check_fn=_check_tool_wrapper("claude_opus_task"),
    description="Claude Opus deep reasoning wrapper",
    emoji="🧠",
)

registry.register(
    name="claude_review_task",
    toolset="external_llm",
    schema=_schema(
        "claude_review_task",
        "Route PR, code, QA, security, and adversarial reviews to Claude Review. Use before reporting code as production-ready.",
        default_timeout=600,
    ),
    handler=lambda args, **kw: _run_wrapper("claude_review_task", args, 600),
    check_fn=_check_tool_wrapper("claude_review_task"),
    description="Claude review wrapper",
    emoji="🔎",
)

registry.register(
    name="codex_web_research",
    toolset="external_llm",
    schema=_schema(
        "codex_web_research",
        "Route current web facts, official docs, pricing, changelogs, and citation-needed research to Codex with web_search enabled.",
        prompt_name="query",
        default_timeout=180,
    ),
    handler=lambda args, **kw: _run_wrapper("codex_web_research", args, 180),
    check_fn=_check_tool_wrapper("codex_web_research"),
    description="Codex web research wrapper",
    emoji="🌐",
)

registry.register(
    name="codex_consult_task",
    toolset="external_llm",
    schema=_schema(
        "codex_consult_task",
        "Route second opinions, adversarial non-code critique, and alternative reasoning to Codex consult.",
        default_timeout=300,
    ),
    handler=lambda args, **kw: _run_wrapper("codex_consult_task", args, 300),
    check_fn=_check_tool_wrapper("codex_consult_task"),
    description="Codex consult wrapper",
    emoji="🤔",
)

registry.register(
    name="gemini_scan_task",
    toolset="external_llm",
    schema=_schema(
        "gemini_scan_task",
        "Route huge repo scans, many-file summarization, multimodal/PDF/image/video analysis, and broad context reconnaissance to Gemini CLI.",
        default_timeout=600,
    ),
    handler=lambda args, **kw: _run_wrapper("gemini_scan_task", args, 600),
    check_fn=_check_tool_wrapper("gemini_scan_task"),
    description="Gemini large-context scan wrapper",
    emoji="📚",
)
