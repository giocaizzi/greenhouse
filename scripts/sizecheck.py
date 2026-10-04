#!/usr/bin/env python3
"""Size checker for the repository's size rules (``make sizecheck``; AGENTS.md "Size and complexity").

Reports, for the given Python files or directories (default: ``libs/``, excluding
``migrations/versions/``):

* every function or method with more than ``MAX_BODY_LINES`` body lines, or nesting deeper than ``MAX_NESTING``;
* every file longer than ``MAX_FILE_LINES`` lines.

Metrics:

* **Body lines** — from the first statement after the docstring to the function's last line. The ``def`` line,
  decorators, signature and docstring are not counted (route / Typer docstrings and signatures are frozen contract
  text). Nested ``def``s count towards the enclosing body and are also measured on their own.
* **Nesting** — maximum depth of nested compound statements (``if``/``for``/``while``/``with``/``try``/``match``,
  async variants included) inside the body. An ``elif`` sits at the depth of its ``if``; nested ``def``/``class``
  bodies are measured separately (they do not add to the enclosing function's nesting).

Cyclomatic complexity is ruff's job (``C90`` at max-complexity 8), not this script's.

Entries listed in ``scripts/size-exceptions.txt`` are reported as ``excepted: <reason>`` and do not fail the run.
Register format, one entry per line (``#`` comments allowed)::

    <repo-relative path>::<qualname> — <reason>     # a function
    <repo-relative path> — <reason>                 # a whole file (> MAX_FILE_LINES)

Exit status: 0 when every hit is excepted, 1 otherwise (2 on usage errors).

Usage::

    uv run python scripts/sizecheck.py                      # all of libs/
    uv run python scripts/sizecheck.py path/to/a.py dir/    # specific files / directories
    uv run python scripts/sizecheck.py --no-exceptions FILE  # ignore the register (check a change in isolation)
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "scripts" / "size-exceptions.txt"
DEFAULT_TARGETS = ("libs",)
EXCLUDED_PARTS = ("migrations/versions/", "/__pycache__/")
MAX_BODY_LINES = 40
MAX_NESTING = 3
MAX_FILE_LINES = 400
SEPARATOR = " — "

COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.TryStar, ast.Match)


@dataclass(frozen=True)
class Hit:
    """One function or file over a size limit."""

    key: str  # "path::qualname" or "path"
    detail: str


def _body_start(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.stmt | None:
    body = func.body
    has_docstring = (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    rest = body[1:] if has_docstring else body
    return rest[0] if rest else None


def body_lines(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Lines from the first statement after the docstring to the end of the function."""
    first = _body_start(func)
    if first is None or func.end_lineno is None:
        return 0
    decorators: list[ast.expr] = getattr(first, "decorator_list", [])
    start = min([first.lineno, *(d.lineno for d in decorators)])
    return func.end_lineno - start + 1


def _child_blocks(node: ast.stmt, lines: list[str]) -> Iterator[tuple[list[ast.stmt], bool]]:
    """Yield (statement block, nests deeper?) pairs for a compound statement."""
    if isinstance(node, ast.If):
        yield node.body, True
        orelse = node.orelse
        is_elif = (
            len(orelse) == 1
            and isinstance(orelse[0], ast.If)
            and lines[orelse[0].lineno - 1].lstrip().startswith("elif")
        )
        yield orelse, not is_elif  # an `elif` stays at the depth of its `if`
    elif isinstance(node, ast.Match):
        for case in node.cases:
            yield case.body, True
    else:
        for name in ("body", "orelse", "finalbody"):
            yield getattr(node, name, []), True
        for handler in getattr(node, "handlers", []):
            yield handler.body, True


def _depth(block: list[ast.stmt], lines: list[str]) -> int:
    deepest = 0
    for stmt in block:
        if isinstance(stmt, COMPOUND):
            for child, deeper in _child_blocks(stmt, lines):
                inner = _depth(child, lines)
                deepest = max(deepest, inner + 1 if deeper else inner)
    return deepest


def nesting(func: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> int:
    """Maximum depth of nested compound statements; nested scopes are measured on their own."""
    return _depth(func.body, lines)


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{child.name}"
                yield qual, child
                yield from walk(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def scan_file(path: Path) -> list[Hit]:
    """Return the size-limit hits of one Python file (file length, then each function)."""
    rel = path.resolve().relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    hits: list[Hit] = []
    if len(lines) > MAX_FILE_LINES:
        hits.append(Hit(rel, f"file lines={len(lines)} > {MAX_FILE_LINES}"))
    for qual, func in _functions(ast.parse(source, filename=rel)):
        size, depth = body_lines(func), nesting(func, lines)
        if size > MAX_BODY_LINES or depth > MAX_NESTING:
            hits.append(Hit(f"{rel}::{qual}", f"body={size} nesting={depth}"))
    return hits


def iter_files(targets: list[str]) -> Iterator[Path]:
    """Yield the Python files under the targets, skipping generated migrations and caches."""
    for target in targets:
        path = (ROOT / target) if not Path(target).is_absolute() else Path(target)
        candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in candidates:
            if not any(part in file.as_posix() for part in EXCLUDED_PARTS):
                yield file


def load_register(path: Path) -> dict[str, str]:
    """Map entry key -> reason. A line without a reason is invalid and reported."""
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, reason = line.partition(SEPARATOR)
        if not sep or not reason.strip():
            msg = f"{path.name}:{number}: entry without a reason: {line!r}"
            raise SystemExit(msg)
        entries[key.strip()] = reason.strip()
    return entries


def main(argv: list[str] | None = None) -> int:
    """Scan the targets and print every hit; return 1 when a hit is not in the register."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help="files or directories (default: libs/)")
    parser.add_argument("--register", type=Path, default=REGISTER, help="exception register")
    parser.add_argument("--no-exceptions", action="store_true", help="ignore the exception register")
    parser.add_argument("--quiet-excepted", action="store_true", help="do not print excepted entries")
    args = parser.parse_args(argv)

    register = {} if args.no_exceptions else load_register(args.register)
    failures = 0
    for file in iter_files(args.targets or list(DEFAULT_TARGETS)):
        for hit in scan_file(file):
            reason = register.get(hit.key)
            if reason is None:
                failures += 1
                print(f"{hit.key}  {hit.detail}")
            elif not args.quiet_excepted:
                print(f"{hit.key}  {hit.detail}  excepted: {reason}")
    if failures:
        print(f"sizecheck: {failures} function(s)/file(s) over the size limits", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
