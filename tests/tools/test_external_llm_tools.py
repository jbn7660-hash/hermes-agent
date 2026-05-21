"""Tests for external LLM wrapper tools."""

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.external_llm_tools import (
    _check_external_llm_wrappers,
    _extract_file,
    _run_wrapper,
    _safe_workdir,
    _WRAPPERS,
)
from tools.registry import registry

_ALL_TOOL_NAMES = [
    "claude_code_task",
    "claude_plan_task",
    "claude_opus_task",
    "claude_review_task",
    "codex_web_research",
    "codex_consult_task",
    "gemini_scan_task",
]


class TestRegistration:
    def test_all_tools_registered(self):
        for name in _ALL_TOOL_NAMES:
            assert name in registry._tools, f"{name} not in registry"

    def test_all_in_external_llm_toolset(self):
        for name in _ALL_TOOL_NAMES:
            entry = registry._tools[name]
            assert entry.toolset == "external_llm"

    def test_schemas_have_required_fields(self):
        for name in _ALL_TOOL_NAMES:
            schema = registry._tools[name].schema
            assert schema["name"] == name
            assert "description" in schema
            params = schema["parameters"]
            assert params["type"] == "object"
            # Each tool has at least one required prompt/query param
            required = params.get("required", [])
            assert len(required) >= 1

    def test_codex_web_research_uses_query_param(self):
        schema = registry._tools["codex_web_research"].schema
        assert "query" in schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["query"]

    def test_claude_code_task_has_max_turns(self):
        schema = registry._tools["claude_code_task"].schema
        assert "max_turns" in schema["parameters"]["properties"]

    def test_workdir_and_timeout_in_all_schemas(self):
        for name in _ALL_TOOL_NAMES:
            props = registry._tools[name].schema["parameters"]["properties"]
            assert "workdir" in props, f"{name} missing workdir"
            assert "timeout_seconds" in props, f"{name} missing timeout_seconds"


class TestCheckFn:
    def test_returns_false_when_wrappers_missing(self):
        with patch.dict(
            "tools.external_llm_tools._WRAPPERS",
            {k: Path("/nonexistent/path/missing") for k in _WRAPPERS},
        ):
            assert _check_external_llm_wrappers() is False

    def test_returns_true_when_all_wrappers_exist(self, tmp_path):
        fake_wrappers = {}
        for name in _WRAPPERS:
            p = tmp_path / name
            p.write_text("#!/bin/sh\necho ok")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            fake_wrappers[name] = p

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake_wrappers):
            assert _check_external_llm_wrappers() is True

    def test_returns_false_when_not_executable(self, tmp_path):
        fake_wrappers = {}
        for name in _WRAPPERS:
            p = tmp_path / name
            p.write_text("#!/bin/sh\necho ok")
            p.chmod(0o644)  # not executable
            fake_wrappers[name] = p

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake_wrappers):
            assert _check_external_llm_wrappers() is False


class TestHelpers:
    def test_extract_file_parses_file_line(self):
        stdout = "Starting...\nfile: /tmp/result-abc.md\nDone."
        assert _extract_file(stdout) == "/tmp/result-abc.md"

    def test_extract_file_returns_none_when_absent(self):
        assert _extract_file("no file line here") is None

    def test_extract_file_returns_none_on_empty(self):
        assert _extract_file("") is None

    def test_safe_workdir_returns_none_for_empty(self):
        assert _safe_workdir(None) is None
        assert _safe_workdir("") is None

    def test_safe_workdir_raises_for_nonexistent(self):
        with pytest.raises(ValueError, match="does not exist"):
            _safe_workdir("/totally/nonexistent/path/xyz")

    def test_safe_workdir_resolves_real_dir(self, tmp_path):
        result = _safe_workdir(str(tmp_path))
        assert result == str(tmp_path.resolve())


class TestRunWrapper:
    def _make_fake_wrapper(self, tmp_path, name, stdout="", returncode=0):
        script = tmp_path / name
        script.write_text(
            f'#!/bin/sh\necho "{stdout}"\nexit {returncode}\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def test_missing_prompt_returns_error(self, tmp_path):
        fake = {k: self._make_fake_wrapper(tmp_path, k) for k in _WRAPPERS}
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            result = json.loads(_run_wrapper("claude_code_task", {}, 300))
        assert result["success"] is False
        assert "required" in result["error"]

    def test_missing_wrapper_returns_error(self, tmp_path):
        fake = dict(_WRAPPERS)
        fake["claude_code_task"] = tmp_path / "missing_script"
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            result = json.loads(_run_wrapper("claude_code_task", {"prompt": "hi"}, 300))
        assert result["success"] is False
        assert "missing" in result["error"]

    def test_successful_run(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_code_task", stdout="done")
        fake = {**_WRAPPERS, "claude_code_task": wrapper}
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            result = json.loads(_run_wrapper("claude_code_task", {"prompt": "fix bug"}, 300))
        assert result["success"] is True
        assert result["tool"] == "claude_code_task"
        assert result["returncode"] == 0
        assert "done" in result["stdout"]

    def test_nonzero_exit_marks_success_false(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_code_task", returncode=1)
        fake = {**_WRAPPERS, "claude_code_task": wrapper}
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            result = json.loads(_run_wrapper("claude_code_task", {"prompt": "hi"}, 300))
        assert result["success"] is False
        assert result["returncode"] == 1

    def test_timeout_capped_at_1800(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_code_task")
        fake = {**_WRAPPERS, "claude_code_task": wrapper}
        captured = {}

        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return real_run(cmd, **kwargs)

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            with patch("tools.external_llm_tools.subprocess.run", side_effect=fake_run):
                _run_wrapper("claude_code_task", {"prompt": "hi", "timeout_seconds": 9999}, 300)

        assert captured["timeout"] == 1800

    def test_max_turns_sets_env_for_claude_code(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_code_task")
        fake = {**_WRAPPERS, "claude_code_task": wrapper}
        captured_env = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            with patch("tools.external_llm_tools.subprocess.run", side_effect=fake_run):
                _run_wrapper("claude_code_task", {"prompt": "hi", "max_turns": 5}, 300)

        assert captured_env.get("HERMES_CLAUDE_CODE_MAX_TURNS") == "5"

    def test_invalid_max_turns_falls_back_for_claude_code(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_code_task")
        fake = {**_WRAPPERS, "claude_code_task": wrapper}
        captured_env = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            with patch("tools.external_llm_tools.subprocess.run", side_effect=fake_run):
                result = json.loads(_run_wrapper("claude_code_task", {"prompt": "hi", "max_turns": "not-a-number"}, 300))

        assert result["success"] is True
        assert captured_env.get("HERMES_CLAUDE_CODE_MAX_TURNS") == "80"

    def test_max_turns_not_set_for_other_tools(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "gemini_scan_task")
        fake = {**_WRAPPERS, "gemini_scan_task": wrapper}
        baseline_has_key = "HERMES_CLAUDE_CODE_MAX_TURNS" in os.environ
        captured_env = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="", stderr="")

        clean_env = {k: v for k, v in os.environ.items() if k != "HERMES_CLAUDE_CODE_MAX_TURNS"}
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            with patch("tools.external_llm_tools.subprocess.run", side_effect=fake_run):
                with patch.dict(os.environ, clean_env, clear=True):
                    _run_wrapper("gemini_scan_task", {"prompt": "scan", "max_turns": 5}, 600)

        assert "HERMES_CLAUDE_CODE_MAX_TURNS" not in captured_env

    def test_output_file_extracted_from_stdout(self, tmp_path):
        outfile = tmp_path / "result.md"
        outfile.write_text("# output")
        wrapper = self._make_fake_wrapper(
            tmp_path, "claude_review_task",
            stdout=f"file: {outfile}"
        )
        fake = {**_WRAPPERS, "claude_review_task": wrapper}
        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            result = json.loads(_run_wrapper("claude_review_task", {"prompt": "review"}, 600))
        assert result["output_file"] == str(outfile)

    def test_timeout_returns_error(self, tmp_path):
        fake = dict(_WRAPPERS)
        fake["codex_web_research"] = tmp_path / "fake"  # doesn't matter, we mock subprocess

        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=1)

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            # Make wrapper appear to exist/executable
            p = tmp_path / "fake"
            p.write_text("#!/bin/sh")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
            fake["codex_web_research"] = p
            with patch("tools.external_llm_tools.subprocess.run", side_effect=raise_timeout):
                result = json.loads(_run_wrapper("codex_web_research", {"query": "test"}, 1))
        assert result["success"] is False
        assert "timed out" in result["error"]

    def test_workdir_passed_to_subprocess(self, tmp_path):
        wrapper = self._make_fake_wrapper(tmp_path, "claude_plan_task")
        fake = {**_WRAPPERS, "claude_plan_task": wrapper}
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            return MagicMock(returncode=0, stdout="", stderr="")

        workdir = tmp_path / "project"
        workdir.mkdir()

        with patch.dict("tools.external_llm_tools._WRAPPERS", fake):
            with patch("tools.external_llm_tools.subprocess.run", side_effect=fake_run):
                _run_wrapper("claude_plan_task", {"prompt": "plan", "workdir": str(workdir)}, 600)

        assert captured["cwd"] == str(workdir.resolve())
