"""Proactive context rollover helpers.

This module does not perform delegation itself.  It builds a deterministic
handoff nudge that the main agent can inject when a session is getting large,
so long-running work is moved to a fresh ``delegate_task`` child before normal
context compression becomes necessary.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ContextUsageSnapshot:
    """Current context-window usage for a turn."""

    prompt_tokens: int
    context_length: int
    percent: float
    source: str = "estimated"


@dataclass(frozen=True)
class ContextRolloverConfig:
    """Config for proactive fresh-session rollover."""

    enabled: bool = False
    warn_threshold: float = 0.30
    rollover_threshold: float = 0.40
    hard_threshold: float = 0.60
    mode: str = "fresh_subagent"
    max_handoff_messages: int = 12

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "ContextRolloverConfig":
        if not isinstance(data, Mapping):
            data = {}

        def _bool(value: Any, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        def _float(name: str, default: float) -> float:
            try:
                value = float(data.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(0.0, min(value, 1.0))

        def _int(name: str, default: int) -> int:
            try:
                value = int(data.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(1, value)

        warn = _float("warn_threshold", cls.warn_threshold)
        rollover = _float("rollover_threshold", cls.rollover_threshold)
        hard = _float("hard_threshold", cls.hard_threshold)
        # Keep thresholds monotonic even if the user config is sloppy.
        rollover = max(rollover, warn)
        hard = max(hard, rollover)

        mode = str(data.get("mode", cls.mode) or cls.mode).strip().lower()
        if mode not in {"fresh_subagent", "compress_only"}:
            mode = cls.mode

        return cls(
            enabled=_bool(data.get("enabled"), cls.enabled),
            warn_threshold=warn,
            rollover_threshold=rollover,
            hard_threshold=hard,
            mode=mode,
            max_handoff_messages=_int("max_handoff_messages", cls.max_handoff_messages),
        )


def build_usage_snapshot(
    *, prompt_tokens: int, context_length: int, source: str = "estimated"
) -> ContextUsageSnapshot:
    prompt_tokens = max(0, int(prompt_tokens or 0))
    context_length = max(0, int(context_length or 0))
    percent = 0.0 if context_length <= 0 else prompt_tokens / context_length
    percent = max(0.0, min(percent, 1.0))
    return ContextUsageSnapshot(
        prompt_tokens=prompt_tokens,
        context_length=context_length,
        percent=percent,
        source=source or "estimated",
    )


_LONG_RUNNING_KEYWORDS = {
    "implement", "fix", "debug", "refactor", "test", "build", "run", "inspect",
    "investigate", "analyze", "review", "plan", "write", "modify", "edit", "create",
    "deploy", "코드", "수정", "구현", "디버그", "테스트", "검증", "분석", "리팩토링",
    "적용", "진행", "고쳐", "만들", "작성", "파악", "조사", "원인", "진단",
}
_ASCII_LONG_RUNNING_KEYWORDS = frozenset(k for k in _LONG_RUNNING_KEYWORDS if k.isascii())
_NON_ASCII_LONG_RUNNING_KEYWORDS = frozenset(k for k in _LONG_RUNNING_KEYWORDS if not k.isascii())

_SHORT_REPLY_PHRASES = {
    "ok", "okay", "thanks", "thank you", "thx", "네", "응", "ㅇㅋ", "고마워", "감사", "확인",
}


def _looks_long_running(user_message: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False
    if text in _SHORT_REPLY_PHRASES:
        return False
    if len(text) >= 80:
        return True
    if any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in _ASCII_LONG_RUNNING_KEYWORDS):
        return True
    return any(keyword in text for keyword in _NON_ASCII_LONG_RUNNING_KEYWORDS)


def _message_text(msg: Mapping[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return str(content) if content is not None else ""


def _format_tail(messages: list[Mapping[str, Any]], max_messages: int) -> str:
    tail = messages[-max_messages:] if max_messages > 0 else []
    lines: list[str] = []
    for msg in tail:
        role = str(msg.get("role") or "unknown")
        name = msg.get("name")
        label = f"{role}:{name}" if name else role
        text = _message_text(msg).replace("\n", " ").strip()
        if len(text) > 800:
            text = text[:800] + "…"
        if text:
            lines.append(f"- {label}: {text}")
    return "\n".join(lines) if lines else "- No prior transcript tail available."


def build_handoff_prompt(
    *,
    snapshot: ContextUsageSnapshot,
    messages: list[Mapping[str, Any]],
    user_message: str,
    cwd: str,
    max_messages: int = 12,
) -> str:
    percent = snapshot.percent * 100
    tail = _format_tail(messages, max_messages=max_messages)
    return (
        "CONTEXT ROLLOVER HANDOFF\n"
        "The current session is getting large. Preserve the parent session's reasoning quality by moving substantial continued work to a fresh child session.\n\n"
        "## Rollover Trigger\n"
        f"- Context usage: {percent:.1f}% ({snapshot.prompt_tokens:,}/{snapshot.context_length:,} tokens, {snapshot.source})\n"
        f"- Working directory: {cwd or '(unknown)'}\n"
        "- Preferred action: call `delegate_task` with the handoff below if this request requires tool use, coding, debugging, research, or multi-step execution.\n"
        "- If the user request is a short conversational answer, answer directly and do not delegate.\n\n"
        "## Current User Request\n"
        f"{user_message or '(empty)'}\n\n"
        "## Recent Transcript Tail\n"
        f"{tail}\n\n"
        "## Delegate Instructions\n"
        "When delegating, pass a self-contained prompt that includes: goal, working directory, relevant transcript tail, files/resources already discovered, commands/results, constraints, and verification required. The child must return Outcome, Summary, Changes, Verification, Remaining Work, and Parent Should Say.\n"
        "Do not send external messages, publish, deploy, or run destructive commands from the child unless the user explicitly requested that side effect."
    )


def maybe_build_rollover_handoff(
    *,
    config: ContextRolloverConfig,
    snapshot: ContextUsageSnapshot,
    messages: list[Mapping[str, Any]],
    user_message: str,
    cwd: str,
    valid_tool_names: Iterable[str],
) -> Optional[str]:
    if not config.enabled:
        return None
    if config.mode == "compress_only":
        return None
    if snapshot.percent < config.rollover_threshold:
        return None
    valid_names = set(valid_tool_names or [])
    if config.mode == "fresh_subagent" and "delegate_task" not in valid_names:
        return None
    if not _looks_long_running(user_message):
        return None
    return build_handoff_prompt(
        snapshot=snapshot,
        messages=messages,
        user_message=user_message,
        cwd=cwd,
        max_messages=config.max_handoff_messages,
    )
