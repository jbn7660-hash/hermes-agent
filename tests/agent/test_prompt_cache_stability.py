"""Byte-equality guard for the agent's system-prompt assembly.

Why this exists
---------------
The plan ``hermes-monolith-split-2026-05-20`` extracts module-level
helpers out of ``run_agent.py``, ``gateway/run.py`` and ``cli.py`` into
small ``_helpers/`` packages.  Each extraction MUST be byte-identical at
the wire — the upstream prefix-cache KV (Anthropic, Bedrock, OpenAI
Responses, OpenRouter) hashes the system prompt + tool schemas exactly.
A single space or reordered block invalidates every cached token,
silently doubling per-turn cost.

Phase 0 of the plan demands a stability test (PR description P3 +
plan-gate Critic M-prerequisite).  This file is it.

What we test
------------
We construct an ``AIAgent`` via the same code path the agent uses (no
context files, no memory, no profile override, ``enabled_toolsets`` =
``["files"]`` so we always pull exactly that toolset's tools).  We then
call ``agent._build_system_prompt(...)`` — which forwards into
``agent.system_prompt.build_system_prompt`` per the cache-invariant
chain documented in that module — and capture both:

  1. the resulting system-prompt string
  2. a canonical ``messages[]`` list (system + user + assistant tool
     call + tool response) wrapped around it

We hash each ``content`` string with SHA-256 and compare against the
fixture in ``tests/agent/fixtures/prompt_cache_hashes.json``.  First
run (or with ``--update-fixtures``) writes the fixture; every
subsequent run asserts byte-equality.

Determinism
-----------
The system prompt's "volatile" tier includes a wall-clock date line
("Conversation started: %A, %B %d, %Y").  Without intervention the test
would still pass within a single day but fail every midnight.  We patch
``hermes_time.now`` to a fixed UTC datetime so the date line is stable
across days, hosts, and CI shards.

We also blank ``TERMINAL_CWD`` and ``HERMES_KANBAN_TASK`` env vars
(both can mutate the context tier / kanban guidance) and force
``skip_context_files=True`` + ``skip_memory=True`` on the agent
itself.  The model + provider strings are pinned ("claude-3-5-sonnet-
20241022", "anthropic") because they appear verbatim in the trailing
volatile line.

If you find this test broke
---------------------------
* Run with ``--update-fixtures`` ONLY if you intend to invalidate the
  upstream prompt cache.  Every breakage of this test is a wire-
  visible change; the cost amortises over millions of turns.
* Inspect the failure diff — the test prints the failing message's
  content snippet alongside the expected vs actual hash so you can
  pinpoint the change quickly.
* If determinism itself broke (e.g. a new wall-clock or random ID
  crept into the prompt), patch / pin the source of nondeterminism
  here rather than weaken the assertion.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "prompt_cache_hashes.json"

# A fixed UTC instant: pins the "Conversation started: %A, %B %d, %Y"
# line in the system prompt's volatile tier.  Any change here will
# invalidate the fixture (intentionally: that line IS the wire payload).
FIXED_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _ensure_fixture_dir() -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_canonical_messages() -> List[Dict[str, Any]]:
    """Return the fixed conversation: system + user + tool call + tool result.

    Why these specific shapes
    -------------------------
    The system message is the assembled prompt from the canonical
    builder chain (forwarder in ``run_agent.py`` ->
    ``agent.system_prompt.build_system_prompt``).  We use that same
    forwarder; do NOT bypass it, because the byte-shape of the
    forwarder + builder is exactly what the cache key hashes.
    """
    # Clear env vars that affect the context/volatile tiers.  We do
    # this here (not at module-import time) so other tests' patches
    # don't bleed in.
    for var in ("TERMINAL_CWD", "HERMES_KANBAN_TASK"):
        os.environ.pop(var, None)

    # Patch the wall-clock so the date line is stable.  ``agent.system_
    # prompt`` does ``from hermes_time import now as _hermes_now``
    # inside the function body, so patching ``hermes_time.now`` at
    # import time is reliable.
    import hermes_time  # noqa: E402  (delayed import; needs env first)

    original_now = hermes_time.now

    def _fixed_now():
        return FIXED_NOW

    hermes_time.now = _fixed_now  # type: ignore[assignment]
    try:
        # Same import-time mock stack as ``tests/run_agent/test_run_agent.py``
        # (canonical pattern for AIAgent construction in CI).
        with patch("run_agent.check_toolset_requirements", return_value={}), \
             patch("run_agent.OpenAI"):
            import run_agent  # noqa: E402

            agent = run_agent.AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                # Pinning model + provider here because they appear
                # verbatim in the trailing volatile line.  Picking a
                # well-known long-context model so AIAgent.__init__'s
                # context-window check passes without a config.yaml
                # override.
                model="claude-3-5-sonnet-20241022",
                provider="anthropic",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                enabled_toolsets=["files"],
            )
            agent.client = None  # belt-and-suspenders: no network
            system_prompt = agent._build_system_prompt("test system message")
    finally:
        hermes_time.now = original_now  # type: ignore[assignment]

    # Canned conversation: system + user + assistant(tool_call) + tool.
    # The shapes match what the AIAgent's request loop sends upstream
    # — i.e. exactly what gets cache-keyed.
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Please list the files in /tmp."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_PROMPT_CACHE_FIXED_1",
                    "type": "function",
                    "function": {
                        "name": "exec",
                        "arguments": json.dumps({"command": "ls /tmp"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_PROMPT_CACHE_FIXED_1",
            "name": "exec",
            "content": "file_a.txt\nfile_b.txt\n",
        },
    ]


def _content_string(message: Dict[str, Any]) -> str:
    """Serialise a message's ``content`` into a stable string.

    For the assistant turn we hash the tool_calls JSON too — that's
    part of the wire-shape and a change there would invalidate the
    cache just as surely as a system-prompt change.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        base = content
    else:
        # list[dict] (multimodal) etc.  json.dumps with sort_keys gives
        # us a deterministic shape regardless of dict ordering.
        base = json.dumps(content, sort_keys=True, ensure_ascii=False)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        base = base + "\n[tool_calls]\n" + json.dumps(
            tool_calls, sort_keys=True, ensure_ascii=False
        )
    return base


def _compute_hashes(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for msg in messages:
        out.append({
            "role": msg["role"],
            "sha256": _hash(_content_string(msg)),
        })
    return out


def _update_fixtures_flag() -> bool:
    # Mirrors the convention used by ``tests/refactor/test_module_init_
    # snapshot.py``: ``--update-fixtures`` on the pytest command line
    # writes a new baseline instead of asserting.
    return "--update-fixtures" in sys.argv


def test_prompt_cache_byte_stability(pytestconfig) -> None:
    """The assembled system prompt + canned conversation hash to the fixture."""
    messages = _build_canonical_messages()
    current_hashes = _compute_hashes(messages)

    update = pytestconfig.getoption("--update-fixtures", default=False) \
        if hasattr(pytestconfig, "getoption") else _update_fixtures_flag()

    if update or not FIXTURE_PATH.exists():
        _ensure_fixture_dir()
        FIXTURE_PATH.write_text(
            json.dumps(
                {
                    "fixed_now_iso": FIXED_NOW.isoformat(),
                    "messages": current_hashes,
                },
                indent=2,
            ) + "\n"
        )
        if not update:
            pytest.skip(
                f"first run: baseline written to {FIXTURE_PATH}. "
                "Re-run the test (without --update-fixtures) to assert "
                "byte-equality."
            )
        return

    expected = json.loads(FIXTURE_PATH.read_text())
    expected_hashes = expected["messages"]
    expected_now = expected.get("fixed_now_iso")
    if expected_now != FIXED_NOW.isoformat():
        pytest.fail(
            f"FIXED_NOW drift: fixture pins {expected_now!r}, "
            f"test pins {FIXED_NOW.isoformat()!r}.  Run with "
            "--update-fixtures only if this is intentional."
        )

    assert len(current_hashes) == len(expected_hashes), (
        f"message count drift: got {len(current_hashes)}, "
        f"fixture has {len(expected_hashes)}"
    )

    for i, (cur, exp) in enumerate(zip(current_hashes, expected_hashes)):
        if cur["sha256"] != exp["sha256"]:
            # Print a useful diff slice so the failure pinpoints the
            # offending message.
            preview = _content_string(messages[i])
            pytest.fail(
                f"message[{i}] role={cur['role']!r} hash drift\n"
                f"  expected: {exp['sha256']}\n"
                f"  actual:   {cur['sha256']}\n"
                f"  preview (first 400 chars):\n{preview[:400]}\n"
                f"  --- end preview ---\n"
                "If this is an intentional wire-shape change, re-run with "
                "--update-fixtures."
            )
