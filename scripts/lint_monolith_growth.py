#!/usr/bin/env python3
"""Pre-commit / CI lint: forbid adding new top-level defs to the monoliths.

The hermes-agent monolith files (``run_agent.py``, ``gateway/run.py``,
``cli.py``) are scheduled for extraction (see plan
``hermes-monolith-split-2026-05-20``).  In the meantime we want a
ratchet that prevents the monoliths from growing further: every new
function or class added at module scope must instead go into one of the
``_helpers/`` packages.

This script takes a list of monolith file paths as args, counts top-
level ``def`` / ``async def`` / ``class`` statements with ``ast``,
compares against the baseline counts in
``scripts/.monolith_baseline.json``, and fails if the count grew.

Exit codes
----------
  0  every monolith's count is <= baseline (shrinking is fine).
  1  at least one monolith grew.
  2  bad arguments or missing baseline file.

Usage
-----
    # In CI / pre-commit:
    python scripts/lint_monolith_growth.py run_agent.py gateway/run.py cli.py

    # To regenerate the baseline after a deliberate extraction PR:
    python scripts/lint_monolith_growth.py --update-baseline \\
        run_agent.py gateway/run.py cli.py

Stdlib only.  No third-party deps.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


BASELINE_REL = "scripts/.monolith_baseline.json"


def _count_top_level_defs(path: Path) -> Tuple[int, int]:
    """Return ``(top_level_functions, top_level_classes)`` for one file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    funcs = 0
    classes = 0
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs += 1
        elif isinstance(stmt, ast.ClassDef):
            classes += 1
    return funcs, classes


def _parse_argv(argv: List[str]) -> Tuple[List[Path], bool]:
    update = False
    files: List[Path] = []
    for arg in argv:
        if arg == "--update-baseline":
            update = True
        elif arg.startswith("-"):
            print(f"error: unknown flag {arg}", file=sys.stderr)
            sys.exit(2)
        else:
            files.append(Path(arg))
    return files, update


def _load_baseline(baseline_path: Path) -> Dict[str, Dict[str, int]]:
    if not baseline_path.exists():
        return {}
    try:
        return json.loads(baseline_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: malformed baseline {baseline_path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _save_baseline(baseline_path: Path, data: Dict[str, Dict[str, int]]) -> None:
    # Sort keys so the file is review-friendly.
    serialised = json.dumps(data, indent=2, sort_keys=True) + "\n"
    baseline_path.write_text(serialised)


def main(argv: Iterable[str]) -> int:
    files, update = _parse_argv(list(argv))
    if not files:
        print(
            "usage: lint_monolith_growth.py [--update-baseline] "
            "<file> [<file> ...]",
            file=sys.stderr,
        )
        return 2

    # Resolve baseline file relative to this script's repo root.  We
    # assume the script lives in ``<repo>/scripts/`` — that's true in
    # both the main checkout and every worktree.
    repo_root = Path(__file__).resolve().parent.parent
    baseline_path = repo_root / BASELINE_REL

    counts: Dict[str, Dict[str, int]] = {}
    for f in files:
        if not f.exists():
            print(f"error: {f} does not exist", file=sys.stderr)
            return 2
        funcs, classes = _count_top_level_defs(f)
        # Use the path as it was passed in (relative-to-cwd) so the
        # baseline JSON keys stay stable across calling sites.
        key = str(f)
        counts[key] = {"functions": funcs, "classes": classes}

    if update:
        existing = _load_baseline(baseline_path)
        existing.update(counts)
        _save_baseline(baseline_path, existing)
        print(f"baseline updated: {baseline_path}", file=sys.stderr)
        for k, v in counts.items():
            print(f"  {k}: functions={v['functions']} classes={v['classes']}",
                  file=sys.stderr)
        return 0

    baseline = _load_baseline(baseline_path)
    if not baseline:
        print(
            f"error: no baseline at {baseline_path}.  Run with "
            "--update-baseline to create one.",
            file=sys.stderr,
        )
        return 2

    failures: List[str] = []
    for key, current in counts.items():
        prior = baseline.get(key)
        if prior is None:
            failures.append(
                f"{key}: no baseline entry — refusing to lint without one. "
                "Run --update-baseline if this monolith is newly tracked."
            )
            continue
        prior_funcs = int(prior.get("functions", 0))
        prior_classes = int(prior.get("classes", 0))
        if current["functions"] > prior_funcs:
            failures.append(
                f"{key}: top-level FUNCTION count grew "
                f"{prior_funcs} -> {current['functions']}.  "
                "Add new helpers under <module>/_helpers/ instead "
                "(see plan hermes-monolith-split-2026-05-20.md)."
            )
        if current["classes"] > prior_classes:
            failures.append(
                f"{key}: top-level CLASS count grew "
                f"{prior_classes} -> {current['classes']}.  "
                "Add new classes under <module>/_helpers/ instead "
                "(see plan hermes-monolith-split-2026-05-20.md)."
            )

    if failures:
        print("monolith growth lint FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    # Quiet pass.  Print a one-line summary so CI logs show we ran.
    for key, current in counts.items():
        prior = baseline[key]
        print(
            f"OK {key}: functions={current['functions']} (baseline "
            f"{prior['functions']}); classes={current['classes']} "
            f"(baseline {prior['classes']})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
