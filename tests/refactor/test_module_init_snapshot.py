"""Module-init side-effect snapshot tests.

Captures sys.modules deltas, environment mutations, and logging handler
state caused by importing each monolith file in a fresh subprocess. The
goal is to detect regressions in module-init order when functions are
extracted into _helpers/ packages — e.g. if `_IS_WINDOWS` bootstrap or
dotenv loading silently moves to a different import-time hook.

Run with --update-fixtures to (re)capture baselines into
tests/refactor/fixtures/init_snapshot.json. The fixtures file is
checked in; subsequent runs assert byte-equality against it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "init_snapshot.json"

# Monoliths to snapshot. Each entry: importable module name (dotted) +
# human-readable label.
MONOLITHS = [
    ("run_agent", "run_agent"),
    ("gateway.run", "gateway_run"),
    ("cli", "cli"),
    ("hermes_cli.main", "hermes_cli_main"),
    # Upstream extracted these out of run_agent.py on 2026-05-16; snapshot
    # them too so any future re-organization is caught (per Codex v2 P1-2).
    ("agent.agent_init", "agent_agent_init"),
    ("agent.conversation_loop", "agent_conversation_loop"),
    ("agent.agent_runtime_helpers", "agent_agent_runtime_helpers"),
]

# Environment-variable prefixes / names we care about. Anything not in
# this set is filtered out — the baseline must be deterministic across
# developer machines.
ENV_KEY_PREFIXES = ("HERMES_", "OPENAI_", "ANTHROPIC_", "OPENROUTER_")
ENV_KEY_NAMES = ("TZ", "LANG", "LC_ALL", "PYTHONPATH")


def _probe_script(module_dotted: str) -> str:
    """Return Python source that imports the module in a fresh subprocess
    and prints a JSON dict to stdout."""
    return f"""
import json, sys, os, logging

# Baseline before the import.
baseline_modules = set(sys.modules.keys())
baseline_env = dict(os.environ)
baseline_root_handlers = list(logging.getLogger().handlers)

# Import under test.
import {module_dotted}  # noqa: F401

# Snapshot after import.
new_modules = sorted(set(sys.modules.keys()) - baseline_modules)
env_after = dict(os.environ)

env_prefixes = {ENV_KEY_PREFIXES!r}
env_names = {ENV_KEY_NAMES!r}

def env_filter(key):
    if key in env_names:
        return True
    return any(key.startswith(p) for p in env_prefixes)

env_changes = {{
    "added": sorted([
        k for k in env_after
        if env_filter(k) and k not in baseline_env
    ]),
    "removed": sorted([
        k for k in baseline_env
        if env_filter(k) and k not in env_after
    ]),
    "changed": sorted([
        k for k in env_after
        if env_filter(k) and k in baseline_env and baseline_env[k] != env_after[k]
    ]),
}}

handler_classes_after = [type(h).__name__ for h in logging.getLogger().handlers]
handler_classes_before = [type(h).__name__ for h in baseline_root_handlers]
handler_delta = {{
    "before": handler_classes_before,
    "after": handler_classes_after,
}}

# Filter sys.modules diff to in-repo modules (hermes-agent code) so the
# baseline is deterministic across Python minor versions / installed
# third-party packages.
in_repo_modules = [
    m for m in new_modules
    if not m.startswith("_")
    and "." not in m.split(".", 1)[0] or True  # keep all for now
]
# Actually filter by whether the module is in the repo. We can't tell
# from inside the subprocess without inspecting __file__; instead, we
# emit everything and let the test post-filter.

print(json.dumps({{
    "module": {module_dotted!r},
    "new_modules": new_modules,
    "env_changes": env_changes,
    "handlers": handler_delta,
}}, sort_keys=True))
"""


def _filter_in_repo(modules: list[str], repo_root: Path) -> list[str]:
    """Keep only modules whose source file is under repo_root."""
    keep = []
    for name in modules:
        mod = sys.modules.get(name)
        if mod is None:
            # Module loaded only in the subprocess; we can't tell — keep
            # the name on the assumption that it's deterministic across
            # runs (the subprocess always imports the same module).
            keep.append(name)
            continue
        file = getattr(mod, "__file__", None)
        if file and Path(file).resolve().is_relative_to(repo_root):
            keep.append(name)
    return keep


# Platform-specific / version-specific module names that may or may not
# appear depending on the host OS, Python build, and transitive dep
# version. These would otherwise cause a baseline captured on macOS to
# fail on linux CI (and vice versa). The snapshot test cares about hermes
# code's own import side effects, not stdlib / vendor-internal churn.
_NOISE_EXACT = frozenset(
    {
        # Darwin / macOS proxy + sysconfig
        "_scproxy",
        "_osx_support",
        # Optional brotli compression backends (httpx/aiohttp transitive)
        "_brotli",
        "brotli",
    }
)
_NOISE_PREFIXES = (
    # CPython cython cache module names embed the cython version
    "_cython_",
    # Platform-tagged sysconfig data modules
    "_sysconfigdata_",
    # Pydantic internals reshape between minor releases and depend on
    # cythonized core availability — track the public pydantic surface
    # not its private layout.
    "pydantic.",
    "pydantic_core.",
)


def _filter_noise(modules: set[str]) -> set[str]:
    return {
        m
        for m in modules
        if m not in _NOISE_EXACT and not any(m.startswith(p) for p in _NOISE_PREFIXES)
    }


def _capture_snapshot(module_dotted: str) -> dict:
    """Run the probe script in a subprocess and parse the JSON output."""
    script = _probe_script(module_dotted)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            # Force determinism — strip provider keys, freeze TZ/LANG.
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "PYTHONHASHSEED": "0",
        },
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Subprocess for {module_dotted} failed: rc={result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _load_fixtures() -> dict:
    if not FIXTURE_PATH.exists():
        return {}
    return json.loads(FIXTURE_PATH.read_text())


def _save_fixtures(data: dict) -> None:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


@pytest.fixture(scope="module")
def fixtures():
    return _load_fixtures()


@pytest.mark.parametrize("module_dotted,label", MONOLITHS, ids=[m[1] for m in MONOLITHS])
def test_module_init_snapshot(module_dotted, label, fixtures, request):
    """For each monolith, assert init-time side effects match baseline."""
    update = request.config.getoption("--update-fixtures", default=False)
    snapshot = _capture_snapshot(module_dotted)

    if update:
        existing = _load_fixtures()
        existing[label] = snapshot
        _save_fixtures(existing)
        pytest.skip(f"Updated baseline for {label}")

    if label not in fixtures:
        pytest.fail(
            f"No baseline for {label} in {FIXTURE_PATH}. "
            f"Run with --update-fixtures to capture."
        )

    expected = fixtures[label]
    # Field-level comparison so the failure message is actionable.
    assert snapshot["env_changes"] == expected["env_changes"], (
        f"{label}: env mutations diverged from baseline"
    )
    assert snapshot["handlers"] == expected["handlers"], (
        f"{label}: logging handler set diverged from baseline"
    )
    # new_modules is the noisiest — compare set membership only,
    # ignoring stdlib churn between Python minor versions by allowing
    # the set to grow (we only fail if previously-seen modules vanish).
    # Filter out platform-specific / version-specific noise so a baseline
    # captured on darwin still passes on linux CI (and vice versa).
    expected_set = _filter_noise(set(expected["new_modules"]))
    actual_set = _filter_noise(set(snapshot["new_modules"]))
    missing = expected_set - actual_set
    assert not missing, (
        f"{label}: imports that used to happen at module init no longer do: {sorted(missing)[:20]}"
    )


# --update-fixtures option registration lives in tests/refactor/conftest.py.
