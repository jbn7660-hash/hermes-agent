"""Tests for proactive context rollover handoff decisions."""

from agent.context_rollover import (
    ContextRolloverConfig,
    ContextUsageSnapshot,
    build_handoff_prompt,
    build_usage_snapshot,
    maybe_build_rollover_handoff,
)


def test_default_rollover_config_is_safe_disabled():
    cfg = ContextRolloverConfig.from_mapping({})

    assert cfg.enabled is False
    assert cfg.warn_threshold == 0.30
    assert cfg.rollover_threshold == 0.40
    assert cfg.hard_threshold == 0.60
    assert cfg.mode == "fresh_subagent"


def test_default_config_exposes_context_rollover_section():
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["context_rollover"]["enabled"] is False
    assert DEFAULT_CONFIG["context_rollover"]["rollover_threshold"] == 0.40
    assert DEFAULT_CONFIG["context_rollover"]["mode"] == "fresh_subagent"


def test_usage_snapshot_computes_bounded_percent():
    snap = build_usage_snapshot(prompt_tokens=45_000, context_length=100_000, source="actual")

    assert snap.prompt_tokens == 45_000
    assert snap.context_length == 100_000
    assert snap.percent == 0.45
    assert snap.source == "actual"


def test_usage_snapshot_handles_missing_context_length():
    snap = build_usage_snapshot(prompt_tokens=45_000, context_length=0, source="estimated")

    assert snap.percent == 0.0
    assert snap.context_length == 0


def test_rollover_not_built_below_threshold_even_when_enabled():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=39_000, context_length=100_000, percent=0.39)

    handoff = maybe_build_rollover_handoff(
        config=cfg,
        snapshot=snap,
        messages=[{"role": "user", "content": "please refactor this code"}],
        user_message="please refactor this code",
        cwd="/repo",
        valid_tool_names={"delegate_task", "terminal", "read_file"},
    )

    assert handoff is None


def test_rollover_requires_delegate_task_tool_for_fresh_subagent_mode():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=41_000, context_length=100_000, percent=0.41)

    handoff = maybe_build_rollover_handoff(
        config=cfg,
        snapshot=snap,
        messages=[{"role": "user", "content": "please refactor this code"}],
        user_message="please refactor this code",
        cwd="/repo",
        valid_tool_names={"terminal", "read_file"},
    )

    assert handoff is None


def test_rollover_builds_fresh_session_handoff_for_long_running_task():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=42_000, context_length=100_000, percent=0.42)
    messages = [
        {"role": "user", "content": "Investigate the failing build"},
        {"role": "assistant", "content": "I found the failing test in tests/test_app.py."},
        {"role": "tool", "name": "terminal", "content": "pytest failed: AssertionError on line 42"},
    ]

    handoff = maybe_build_rollover_handoff(
        config=cfg,
        snapshot=snap,
        messages=messages,
        user_message="fix the build and run tests",
        cwd="/repo",
        valid_tool_names={"delegate_task", "terminal", "read_file"},
    )

    assert handoff is not None
    assert "CONTEXT ROLLOVER HANDOFF" in handoff
    assert "42.0%" in handoff
    assert "delegate_task" in handoff
    assert "fix the build and run tests" in handoff
    assert "pytest failed" in handoff


def test_rollover_skips_short_conversation_answer():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=50_000, context_length=100_000, percent=0.50)

    handoff = maybe_build_rollover_handoff(
        config=cfg,
        snapshot=snap,
        messages=[{"role": "user", "content": "thanks"}],
        user_message="thanks",
        cwd="/repo",
        valid_tool_names={"delegate_task"},
    )

    assert handoff is None


def test_rollover_long_running_english_keywords_use_word_boundaries():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=50_000, context_length=100_000, percent=0.50)

    for message in ["what is the latest credit", "explanation only", "truncate the text"]:
        handoff = maybe_build_rollover_handoff(
            config=cfg,
            snapshot=snap,
            messages=[{"role": "user", "content": message}],
            user_message=message,
            cwd="/repo",
            valid_tool_names={"delegate_task"},
        )
        assert handoff is None


def test_rollover_korean_short_confirmation_does_not_trigger():
    cfg = ContextRolloverConfig.from_mapping({"enabled": True, "rollover_threshold": 0.40})
    snap = ContextUsageSnapshot(prompt_tokens=50_000, context_length=100_000, percent=0.50)

    handoff = maybe_build_rollover_handoff(
        config=cfg,
        snapshot=snap,
        messages=[{"role": "user", "content": "확인해봐"}],
        user_message="확인해봐",
        cwd="/repo",
        valid_tool_names={"delegate_task"},
    )

    assert handoff is None


def test_build_handoff_prompt_bounds_transcript_tail():
    snap = ContextUsageSnapshot(prompt_tokens=60_000, context_length=100_000, percent=0.60)
    messages = [
        {"role": "user", "content": f"message {idx}"}
        for idx in range(20)
    ]

    prompt = build_handoff_prompt(
        snapshot=snap,
        messages=messages,
        user_message="continue implementation",
        cwd="/repo",
        max_messages=3,
    )

    assert "message 19" in prompt
    assert "message 18" in prompt
    assert "message 17" in prompt
    assert "message 16" not in prompt
