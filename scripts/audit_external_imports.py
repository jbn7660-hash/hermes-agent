#!/usr/bin/env python3
"""Audit every consumer that imports from the monolith files.

For each repo-wide ``.py`` consumer, this script reports every
``import <monolith>`` and ``from <monolith> import …`` statement that
targets one of the monolith modules passed on the command line.

Output is TSV on stdout:

    consumer_file<TAB>line<TAB>import_statement<TAB>imported_symbols

``imported_symbols`` is a comma-separated list of the symbol names the
consumer pulls in.  For ``import run_agent`` the list is the module name
itself (``run_agent``).  For ``import run_agent as foo`` the list is the
alias (``foo``).  For ``from run_agent import IterationBudget, AIAgent``
the list is ``IterationBudget,AIAgent``.

Rows are sorted by symbol so the re-export shim authors can see every
caller of each symbol grouped together (the secondary sort key keeps
the output deterministic across runs).

Why this matters
----------------
Plan v1 of the hermes-monolith-split surfaced ≥ 11 production and test
consumers that directly import helpers from the monolith files (e.g.
``from run_agent import _sanitize_surrogates``).  Each extracted symbol
must keep an explicit re-export shim or the consumer's import will break
with ``ImportError`` at module load time.

Usage
-----
    # default report (excludes tests/)
    python scripts/audit_external_imports.py run_agent gateway.run cli

    # include tests/ as well (some symbols are only test-imported)
    python scripts/audit_external_imports.py --include-tests run_agent gateway.run cli

    # tests-only report
    python scripts/audit_external_imports.py --tests-only run_agent gateway.run cli

Stdlib only.  No third-party deps.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Set, Tuple


# Repo subdirectories we never want to scan, even with --include-tests.
_ALWAYS_EXCLUDE: Tuple[str, ...] = (
    "venv",
    ".venv",
    ".git",
    "node_modules",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    "ui-tui/node_modules",
    "website/node_modules",
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_argv(argv: List[str]) -> Tuple[List[str], str]:
    """Return (monoliths, scope).

    ``scope`` is one of "no-tests" (default), "include-tests", "tests-only".
    """
    monoliths: List[str] = []
    scope = "no-tests"
    for arg in argv:
        if arg == "--include-tests":
            scope = "include-tests"
        elif arg == "--tests-only":
            scope = "tests-only"
        elif arg.startswith("-"):
            print(f"error: unknown flag {arg}", file=sys.stderr)
            sys.exit(2)
        else:
            monoliths.append(arg)
    return monoliths, scope


# ---------------------------------------------------------------------------
# Module-name normalisation
# ---------------------------------------------------------------------------

def _normalise_monolith(name: str) -> str:
    """Accept ``run_agent.py`` / ``gateway/run.py`` / ``cli`` / ``gateway.run``."""
    # Strip ``.py`` and turn ``gateway/run`` into ``gateway.run``.
    if name.endswith(".py"):
        name = name[: -len(".py")]
    return name.replace("/", ".").replace("\\", ".")


# ---------------------------------------------------------------------------
# Path-walk: find .py consumer files
# ---------------------------------------------------------------------------

def _walk_py_files(repo_root: Path, scope: str) -> Iterator[Path]:
    """Yield every ``.py`` file under ``repo_root``, respecting ``scope``."""
    skip_test_dirs = scope == "no-tests"
    only_test_dirs = scope == "tests-only"

    for entry in sorted(repo_root.rglob("*.py")):
        try:
            rel = entry.relative_to(repo_root)
        except ValueError:
            continue
        parts = rel.parts
        # Always-skip subtrees.
        if any(p in _ALWAYS_EXCLUDE for p in parts):
            continue
        # Test-dir scoping.  We treat top-level ``tests/`` as "the tests".
        in_tests = parts and parts[0] == "tests"
        if skip_test_dirs and in_tests:
            continue
        if only_test_dirs and not in_tests:
            continue
        yield entry


# ---------------------------------------------------------------------------
# Import-statement extraction
# ---------------------------------------------------------------------------

def _import_statements(path: Path) -> Iterator[Tuple[int, ast.AST]]:
    """Yield ``(lineno, node)`` for every Import/ImportFrom in ``path``."""
    try:
        source = path.read_text()
    except (OSError, UnicodeDecodeError):
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node.lineno, node


def _format_import_statement(node: ast.AST) -> str:
    """Return a human-readable form of the import statement."""
    if isinstance(node, ast.Import):
        parts = []
        for alias in node.names:
            if alias.asname:
                parts.append(f"{alias.name} as {alias.asname}")
            else:
                parts.append(alias.name)
        return f"import {', '.join(parts)}"
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        dots = "." * (node.level or 0)
        parts = []
        for alias in node.names:
            if alias.asname:
                parts.append(f"{alias.name} as {alias.asname}")
            else:
                parts.append(alias.name)
        return f"from {dots}{module} import {', '.join(parts)}"
    return ""


def _statement_targets_monolith(
    node: ast.AST, monoliths: Set[str]
) -> Tuple[bool, List[str]]:
    """Return ``(matches, imported_symbol_list)`` if the import targets one.

    ``imported_symbol_list`` is the list of NAMES the consumer pulls into
    its namespace (the bound name in the consumer's scope):
      * ``import run_agent``                 -> ["run_agent"]
      * ``import run_agent as foo``          -> ["foo"]
      * ``from run_agent import IB, A``      -> ["IB", "A"]
      * ``from run_agent import IB as X``    -> ["X"]
      * ``from gateway.run import _normalize_empty_agent_response``
                                              -> ["_normalize_empty_agent_response"]

    Relative imports are NOT supported here — every consumer that
    imports from a monolith does so absolutely.
    """
    if isinstance(node, ast.Import):
        symbols: List[str] = []
        matched = False
        for alias in node.names:
            if alias.name in monoliths:
                matched = True
                symbols.append(alias.asname or alias.name)
        return matched, symbols
    if isinstance(node, ast.ImportFrom):
        if (node.level or 0) > 0:
            return False, []  # relative import — out of scope
        if not node.module:
            return False, []
        if node.module not in monoliths:
            return False, []
        symbols = [alias.asname or alias.name for alias in node.names]
        return True, symbols
    return False, []


# ---------------------------------------------------------------------------
# Audit driver
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    monoliths_raw, scope = _parse_argv(argv)
    if not monoliths_raw:
        print(
            "usage: audit_external_imports.py [--include-tests|--tests-only] "
            "<monolith> [<monolith> ...]",
            file=sys.stderr,
        )
        return 2

    monoliths: Set[str] = {_normalise_monolith(m) for m in monoliths_raw}
    repo_root = Path.cwd()

    # Build the row list before sorting.  Sort by (symbol, consumer, line)
    # so the shim author sees all callers of each symbol together.
    rows: List[Tuple[str, str, int, str, str]] = []
    for path in _walk_py_files(repo_root, scope):
        # Skip the monolith files themselves — a monolith importing
        # ``from cli import foo`` is the audit-target case, but in
        # practice the monoliths import their own helpers via
        # ``self``-relative or stdlib paths, not by re-importing
        # themselves.  We exclude them to keep the report focused.
        try:
            rel_norm = _normalise_monolith(str(path.relative_to(repo_root)))
        except ValueError:
            rel_norm = ""
        if rel_norm in monoliths:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            rel = path

        for lineno, node in _import_statements(path):
            matched, symbols = _statement_targets_monolith(node, monoliths)
            if not matched:
                continue
            stmt = _format_import_statement(node)
            symbol_csv = ",".join(symbols)
            # Sort key uses the first symbol; ties broken by file + line.
            first_sym = symbols[0] if symbols else ""
            rows.append((first_sym, str(rel), lineno, stmt, symbol_csv))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    print("consumer_file\tline\timport_statement\timported_symbols")
    for _sort_key, file_, line_, stmt, sym_csv in rows:
        print(f"{file_}\t{line_}\t{stmt}\t{sym_csv}")
    print(f"# total findings: {len(rows)} (scope={scope})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
