"""Transcript-replay helpers extracted from ``gateway/run.py``.

Part of the hermes-monolith-split refactor (Phase 1). Pure functions plus
one module-global tuple (``_ASSISTANT_REPLAY_FIELDS``). No gateway-internal
imports — module-init order stays clean (plan v3 CDX-1).

Why this exists: multi-turn fidelity depends on the gateway echoing back
the assistant fields the model originally returned. The whitelist below
documents which fields must survive transcript replay so reasoning
continuity, prefix-cache hits, and provider-specific echo requirements
all behave the same on the gateway as they do in the CLI.

Assistant-message fields that must survive transcript replay:

* ``reasoning`` and ``reasoning_details`` were the original three preserved
  by PR #2974 (schema v6).  ``reasoning_content``, ``codex_reasoning_items``,
  ``codex_message_items``, and ``finish_reason`` were added to the DB later
  but the gateway's replay whitelist was never expanded to match — so any
  pure-text assistant turn (no ``tool_calls``) silently dropped them on
  replay, regressing the CLI-vs-gateway behavioural parity.

Why each field matters on replay:

* ``reasoning`` / ``reasoning_content``: provider-facing thinking text.
  ``_copy_reasoning_content_for_api`` promotes ``reasoning`` →
  ``reasoning_content`` at send time, but only when the strings happen to
  match.  Carrying the original ``reasoning_content`` verbatim avoids
  reconstruction loss for providers that return them as distinct fields
  (DeepSeek/Kimi/Moonshot thinking modes).
* ``reasoning_details``: opaque structured array (signature,
  encrypted_content) used by OpenRouter/Anthropic to maintain reasoning
  continuity across turns.
* ``codex_reasoning_items``: encrypted reasoning blobs for the OpenAI
  Codex Responses API.
* ``codex_message_items``: exact assistant message items with ``phase``.
  OpenAI docs: "preserve and resend phase on all assistant messages —
  dropping it can degrade performance."  Required for prefix cache hits.
* ``finish_reason``: informational; cheap to keep so transcripts replay
  identically across CLI and gateway.
"""

from typing import Any, Dict, List, Optional

_ASSISTANT_REPLAY_FIELDS: tuple[str, ...] = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "finish_reason",
)


def _build_replay_entry(role: str, content: Any, msg: Dict[str, Any]) -> Dict[str, Any]:
    """Build a replay entry for a non-tool-calling message, preserving the
    assistant fields the agent's API builders rely on for multi-turn fidelity.

    Lifted out of the inline ``run_sync`` closure so the field whitelist can
    be unit-tested in isolation.  Mirrors the ``_ASSISTANT_REPLAY_FIELDS``
    contract above.

    Empty values: most fields are dropped when falsy (matching the original
    PR #2974 behaviour) since an empty list/string for those carries no
    information.  The exception is ``reasoning_content``: DeepSeek/Kimi
    thinking-mode replay treats an empty string as a meaningful sentinel
    that ``_copy_reasoning_content_for_api`` upgrades to a single space.
    Dropping it here would make the gateway send no ``reasoning_content`` at
    all on the next turn, which can cause HTTP 400 from strict thinking
    providers.
    """
    entry: Dict[str, Any] = {"role": role, "content": content}
    if role == "assistant":
        for _rkey in _ASSISTANT_REPLAY_FIELDS:
            if _rkey not in msg:
                continue
            _rval = msg.get(_rkey)
            if _rkey == "reasoning_content":
                # Preserve empty-string sentinel for thinking-mode replay.
                if _rval is None:
                    continue
            elif not _rval:
                continue
            entry[_rkey] = _rval
    return entry


def _last_transcript_timestamp(history: Optional[List[Dict[str, Any]]]) -> Any:
    """Return the ``timestamp`` of the last usable transcript row, if any.

    Skips metadata-only rows (``session_meta``, system injections) that are
    dropped before being handed to the agent.  Returns ``None`` when no
    usable row carries a timestamp — callers should treat that as "fresh"
    for backward compatibility.
    """
    if not history:
        return None
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if not role or role in {"session_meta", "system"}:
            continue
        ts = msg.get("timestamp")
        if ts is not None:
            return ts
        # First non-meta row without a timestamp — legacy transcript row.
        # Returning None lets the caller fall through to the legacy-fresh path.
        return None
    return None
