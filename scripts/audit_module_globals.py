#!/usr/bin/env python3
"""Audit implicit module-global dependencies inside the monolith files.

For each top-level function and class defined in the given Python file(s),
this script walks every function body and reports every ``Name`` reference
that is NOT:

  (a) a function parameter (positional, keyword, *args, **kwargs, posonly,
      kwonly)
  (b) a local variable bound inside the function (assignment, augmented
      assignment, ``for`` target, ``with`` target, walrus, comprehension
      target, ``except as`` target, nested ``def`` / ``async def`` /
      ``class``, ``import`` / ``from import`` local binding,
      ``global`` / ``nonlocal`` declaration)
  (c) a stdlib name (Python builtins + the set of standard-library module
      names that appear in ``import`` / ``from import`` statements at the
      module level of the file being audited)
  (d) a top-level function, class, or variable defined in the SAME module

Output is TSV on stdout:

    file<TAB>function<TAB>unbound_name<TAB>line

Why this matters
----------------
Plan v1 of the hermes monolith split (``hermes-monolith-split-2026-05-20``)
identified module globals like ``_hermes_home``, ``_SURROGATE_RE``,
``cfg_get``, ``_cleanup_done``, ``_active_agent_ref`` etc. that cross
extraction boundaries.  Helpers that look "pure" actually reach back into
the monolith for these names.  Extracting without auditing yields
``NameError`` at import time.  This script surfaces every such reference
so the extraction plan can refactor (accessor helper), move the global
with the function, or skip the function entirely.

Usage
-----
    python scripts/audit_module_globals.py run_agent.py gateway/run.py cli.py

Stdlib only.  No third-party deps.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator, Set, Tuple


# ---------------------------------------------------------------------------
# Stdlib name set
# ---------------------------------------------------------------------------
_PY_BUILTINS: Set[str] = set(
    dir(__builtins__) if isinstance(__builtins__, dict) else dir(__builtins__)
)
# Common builtin names that ``dir(__builtins__)`` may not surface in every
# context (e.g. when this script is executed as ``__main__``).
_PY_BUILTINS.update({
    "True", "False", "None", "NotImplemented", "Ellipsis",
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__import__",
})

_STDLIB_MODULES: Set[str] = set(getattr(sys, "stdlib_module_names", set()))


# ---------------------------------------------------------------------------
# Target-name iteration (assignment LHS, with-as, for-target, ...)
# ---------------------------------------------------------------------------

def _iter_target_names(target: ast.AST) -> Iterator[str]:
    """Yield every ``Name.id`` bound by an assignment-target node."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _iter_target_names(elt)
    elif isinstance(target, ast.Starred):
        yield from _iter_target_names(target.value)
    # Attribute / Subscript targets do not introduce a local binding.


# ---------------------------------------------------------------------------
# Module-level binding collection
# ---------------------------------------------------------------------------

def _collect_module_bindings(tree: ast.Module) -> Tuple[Set[str], Set[str]]:
    """Split module-level bindings into ``(allowed, suspect)``.

    Per the audit spec (Phase 0 of hermes-monolith-split-2026-05-20 plan),
    a Name inside a function body is "bound" if it is:
      (c) a stdlib import (handled by ``_stdlib_imports_in_module``), or
      (d) a function defined in the same module.

    Module-level *variables* (e.g. ``_hermes_home``, ``_SURROGATE_RE``,
    ``cfg_get``) are exactly the kind of cross-module-boundary state the
    plan wants surfaced.  So we keep them OUT of the allowed set.

    ``allowed`` contains:
      * top-level ``def`` / ``async def`` / ``class`` names
      * names bound by top-level ``import`` / ``from import`` statements
        (these are imports — they're external references that the
        extracted helper will simply re-import on its own)
    ``suspect`` contains:
      * names bound by top-level assignments / for / with targets
      * names bound by ``global`` declarations inside compound stmts

    Suspect names are still useful to surface in case a function body
    references them — that's exactly the cross-monolith coupling the
    plan asks us to find.  We return them separately in case a future
    caller wants the breakdown; the audit driver only uses ``allowed``.
    """
    allowed: Set[str] = set()
    suspect: Set[str] = set()

    def _walk(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # def / class names: explicitly allowed per criterion (d).
            allowed.add(node.name)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Imports: explicitly allowed.  Even non-stdlib imports are
            # fine because the extracted helper will re-import them on
            # its own — they're not "module-global cross-boundary state".
            for alias in node.names:
                allowed.add(alias.asname or alias.name.split(".")[0])
            return
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                for n in _iter_target_names(tgt):
                    suspect.add(n)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            suspect.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            suspect.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for n in _iter_target_names(node.target):
                suspect.add(n)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for n in _iter_target_names(item.optional_vars):
                        suspect.add(n)
        elif isinstance(node, ast.Try):
            for h in node.handlers:
                if h.name:
                    suspect.add(h.name)
        elif isinstance(node, ast.Global):
            for n in node.names:
                suspect.add(n)
        # Descend into compound module-level statements.
        if isinstance(node, (ast.If, ast.Try, ast.For, ast.AsyncFor,
                              ast.With, ast.AsyncWith)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    _walk(child)

    if isinstance(tree, ast.Module):
        for stmt in tree.body:
            _walk(stmt)
    return allowed, suspect


def _stdlib_imports_in_module(tree: ast.AST) -> Set[str]:
    """Return the set of stdlib top-level module names imported by the file.

    Accept ``import json`` -> {"json"} and ``from json import dumps``
    -> {"json"} so that references to ``json`` etc. are recognised
    regardless of whether they ended up bound at module scope.
    """
    seen: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _STDLIB_MODULES:
                    seen.add(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _STDLIB_MODULES:
                seen.add(root)
    return seen


# ---------------------------------------------------------------------------
# Per-scope local binding collection (does NOT descend into nested scopes)
# ---------------------------------------------------------------------------

def _function_param_names(func: ast.AST) -> Set[str]:
    """Parameter names introduced by a function / lambda definition."""
    names: Set[str] = set()
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = func.args
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            names.add(a.arg)
        if args.vararg is not None:
            names.add(args.vararg.arg)
        if args.kwarg is not None:
            names.add(args.kwarg.arg)
    return names


def _scope_locals(body: Iterable[ast.AST]) -> Set[str]:
    """Collect names bound inside a function/class body's statements.

    Does NOT descend into nested ``def`` / ``async def`` / ``class`` /
    ``Lambda`` / comprehensions — those introduce new scopes.  Their
    NAMES (the def/class name) DO bind in our scope and are recorded.
    """
    names: Set[str] = set()

    def _record(node: ast.AST) -> None:
        # Examine THIS node for bindings it produces.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            names.add(node.name)
            return  # do not descend — its body is a new scope
        if isinstance(node, (ast.Lambda, ast.GeneratorExp, ast.ListComp,
                              ast.SetComp, ast.DictComp)):
            # New scope — names bound inside do not affect our locals.
            return
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                for n in _iter_target_names(tgt):
                    names.add(n)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for n in _iter_target_names(node.target):
                names.add(n)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for n in _iter_target_names(item.optional_vars):
                        names.add(n)
        elif isinstance(node, ast.Try):
            for h in node.handlers:
                if h.name:
                    names.add(h.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                names.add(n)
        # Recurse into children (e.g. ``if``/``while``/``try`` body
        # statements introduce bindings into our scope).
        for child in ast.iter_child_nodes(node):
            _record(child)

    for stmt in body:
        _record(stmt)
    return names


def _comprehension_target_names(generators: Iterable[ast.comprehension]) -> Set[str]:
    names: Set[str] = set()
    for gen in generators:
        for n in _iter_target_names(gen.target):
            names.add(n)
    return names


# ---------------------------------------------------------------------------
# Scope-bounded name-reference walker
# ---------------------------------------------------------------------------

def _scan_function(
    func: ast.AST,
    allowed: Set[str],
    outer_scopes: Tuple[Set[str], ...] = (),
) -> Iterator[Tuple[str, int]]:
    """Yield ``(unbound_name, lineno)`` for Name(Load) refs inside ``func``.

    The scan threads an immutable stack of enclosing local-scopes through
    nested ``def`` / ``async def`` / ``class`` / ``Lambda`` and
    comprehensions.  A name is considered "bound" if it appears in (a) any
    enclosing scope, or (b) the ``allowed`` set (builtins, module globals,
    stdlib imports).
    """
    params = _function_param_names(func)
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = func.body
    elif isinstance(func, ast.Lambda):
        body = [func.body]
    else:
        body = []
    locals_ = params | _scope_locals(body)
    new_scopes = outer_scopes + (locals_,)

    def _is_bound(name: str) -> bool:
        if name in allowed:
            return True
        for scope in new_scopes:
            if name in scope:
                return True
        return False

    def _walk(node: ast.AST) -> Iterator[Tuple[str, int]]:
        # Nested function / lambda: default args + decorators + annotations
        # evaluate in the ENCLOSING scope; their bodies are a new scope.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in list(node.args.defaults) + list(node.args.kw_defaults):
                if default is None:
                    continue
                yield from _walk(default)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    yield from _walk(deco)
                if node.returns is not None:
                    yield from _walk(node.returns)
                for arg in (list(node.args.posonlyargs)
                            + list(node.args.args)
                            + list(node.args.kwonlyargs)
                            + ([node.args.vararg] if node.args.vararg else [])
                            + ([node.args.kwarg] if node.args.kwarg else [])):
                    if arg.annotation is not None:
                        yield from _walk(arg.annotation)
            yield from _scan_function(node, allowed, new_scopes)
            return
        if isinstance(node, ast.ClassDef):
            for deco in node.decorator_list:
                yield from _walk(deco)
            for base in node.bases:
                yield from _walk(base)
            for kw in node.keywords:
                if kw.value is not None:
                    yield from _walk(kw.value)
            class_locals = _scope_locals(node.body)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield from _scan_function(
                        child, allowed, new_scopes + (class_locals,)
                    )
                else:
                    yield from _walk_general(
                        child, allowed, new_scopes + (class_locals,)
                    )
            return
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp,
                              ast.DictComp)):
            comp_locals = _comprehension_target_names(node.generators)
            comp_scopes = new_scopes + (comp_locals,)
            # First generator's iter evaluates in the enclosing scope.
            for i, gen in enumerate(node.generators):
                if i == 0:
                    yield from _walk(gen.iter)
                else:
                    yield from _walk_general(gen.iter, allowed, comp_scopes)
                for cond in gen.ifs:
                    yield from _walk_general(cond, allowed, comp_scopes)
            if isinstance(node, ast.DictComp):
                yield from _walk_general(node.key, allowed, comp_scopes)
                yield from _walk_general(node.value, allowed, comp_scopes)
            else:
                yield from _walk_general(node.elt, allowed, comp_scopes)
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and not _is_bound(node.id):
                yield (node.id, node.lineno)
            return
        for child in ast.iter_child_nodes(node):
            yield from _walk(child)

    for stmt in body:
        yield from _walk(stmt)


def _walk_general(
    node: ast.AST,
    allowed: Set[str],
    scopes: Tuple[Set[str], ...],
) -> Iterator[Tuple[str, int]]:
    """Walk an arbitrary subtree with a given scope-stack.

    Used for class-body non-method statements and comprehension
    sub-expressions (iter / cond / element).  Recurses into nested
    function / lambda / comprehension scopes correctly.
    """

    def _is_bound(name: str) -> bool:
        if name in allowed:
            return True
        for scope in scopes:
            if name in scope:
                return True
        return False

    def _walk(n: ast.AST) -> Iterator[Tuple[str, int]]:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in list(n.args.defaults) + list(n.args.kw_defaults):
                if default is None:
                    continue
                yield from _walk(default)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in n.decorator_list:
                    yield from _walk(deco)
                if n.returns is not None:
                    yield from _walk(n.returns)
                for arg in (list(n.args.posonlyargs)
                            + list(n.args.args)
                            + list(n.args.kwonlyargs)
                            + ([n.args.vararg] if n.args.vararg else [])
                            + ([n.args.kwarg] if n.args.kwarg else [])):
                    if arg.annotation is not None:
                        yield from _walk(arg.annotation)
            yield from _scan_function(n, allowed, scopes)
            return
        if isinstance(n, ast.ClassDef):
            for deco in n.decorator_list:
                yield from _walk(deco)
            for base in n.bases:
                yield from _walk(base)
            for kw in n.keywords:
                if kw.value is not None:
                    yield from _walk(kw.value)
            class_locals = _scope_locals(n.body)
            for child in n.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield from _scan_function(child, allowed,
                                              scopes + (class_locals,))
                else:
                    yield from _walk_general(child, allowed,
                                             scopes + (class_locals,))
            return
        if isinstance(n, (ast.GeneratorExp, ast.ListComp, ast.SetComp,
                          ast.DictComp)):
            comp_locals = _comprehension_target_names(n.generators)
            inner_scopes = scopes + (comp_locals,)
            for i, gen in enumerate(n.generators):
                if i == 0:
                    yield from _walk(gen.iter)
                else:
                    yield from _walk_general(gen.iter, allowed, inner_scopes)
                for cond in gen.ifs:
                    yield from _walk_general(cond, allowed, inner_scopes)
            if isinstance(n, ast.DictComp):
                yield from _walk_general(n.key, allowed, inner_scopes)
                yield from _walk_general(n.value, allowed, inner_scopes)
            else:
                yield from _walk_general(n.elt, allowed, inner_scopes)
            return
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load) and not _is_bound(n.id):
                yield (n.id, n.lineno)
            return
        for child in ast.iter_child_nodes(n):
            yield from _walk(child)

    yield from _walk(node)


# ---------------------------------------------------------------------------
# Top-level callable iteration + audit driver
# ---------------------------------------------------------------------------

def _iter_top_level_callables(tree: ast.Module) -> Iterator[ast.AST]:
    """Yield top-level functions plus every method inside top-level classes.

    Methods are reported under ``ClassName.method_name``.
    """
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield stmt
        elif isinstance(stmt, ast.ClassDef):
            for child in stmt.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child._qualified_name = f"{stmt.name}.{child.name}"  # type: ignore[attr-defined]
                    yield child


def _qualified_name(func: ast.AST) -> str:
    return getattr(func, "_qualified_name", getattr(func, "name", "<unknown>"))


def _audit_file(path: Path) -> Iterator[Tuple[str, str, str, int]]:
    """Yield ``(file, function, unbound_name, line)`` tuples for one file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_allowed, _module_suspect = _collect_module_bindings(tree)
    stdlib_in_use = _stdlib_imports_in_module(tree)
    # Allowed = builtins + (def/class/imports defined at module scope) +
    # stdlib modules.  Module-level VARIABLES (assignments) are
    # deliberately NOT allowed — surfacing them is the whole point.
    allowed: Set[str] = _PY_BUILTINS | module_allowed | stdlib_in_use

    for func in _iter_top_level_callables(tree):
        seen: Set[Tuple[str, int]] = set()
        for name, lineno in _scan_function(func, allowed):
            key = (name, lineno)
            if key in seen:
                continue
            seen.add(key)
            yield (str(path), _qualified_name(func), name, lineno)


def main(argv: Iterable[str]) -> int:
    paths = [Path(p) for p in argv]
    if not paths:
        print("usage: audit_module_globals.py <file> [<file> ...]", file=sys.stderr)
        return 2

    print("file\tfunction\tunbound_name\tline")
    total = 0
    for path in paths:
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 1
        for file_, func, name, line in _audit_file(path):
            print(f"{file_}\t{func}\t{name}\t{line}")
            total += 1
    print(f"# total findings: {total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
