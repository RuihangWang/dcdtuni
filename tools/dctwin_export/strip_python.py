"""Remove top-level definitions, if/elif branches and statements from Python source, keeping the rest byte-identical.

Used by export.py to cut the display and AI routes out of ``src/server/server.py`` for the dctwin evaluator
export. The cut is made on line ranges taken from the AST, so comments and formatting outside the cut stay as
they are; the result must parse, and none of the removed definitions may still be referenced.
"""
from __future__ import annotations

import ast
import re
from typing import Iterable, List, Optional, Pattern, Set, Tuple


class StripError(RuntimeError):
    pass


def _node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    return min([node.lineno] + [d.lineno for d in decorators])


def _chain(node: ast.If, lines: List[str]) -> Tuple[List[ast.If], List[ast.stmt]]:
    """The branches of the if/elif chain headed by ``node`` and its trailing else body (may be empty)."""
    branches = [node]
    current = node
    while (len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If)
           and lines[current.orelse[0].lineno - 1].lstrip().startswith("elif")):
        current = current.orelse[0]
        branches.append(current)
    return branches, current.orelse


def strip_source(source: str, *, defs: Optional[Pattern[str]] = None, branch_tests: Optional[Pattern[str]] = None,
                 statements: Optional[Pattern[str]] = None, drop_lines: Iterable[Pattern[str]] = ()) -> Tuple[str, dict]:
    """Return ``(new_source, report)``.

    ``defs``: top-level functions/classes whose name fully matches are removed (with their decorators and the
    comment lines directly above them). ``branch_tests``: an if/elif branch whose test's source matches is
    removed; a chain keeps its remaining branches (the first one becomes ``if``). ``statements``: any other
    simple statement whose source matches is removed. ``drop_lines``: single lines removed by regex. A block left
    empty by these removals is removed with its parent statement.
    """
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    removed: Set[int] = set()  # 1-based line numbers
    rewrites = {}
    report = {"defs": [], "branches": [], "statements": [], "lines": []}

    def cut(a: int, b: int) -> None:
        removed.update(range(a, b + 1))

    if defs is not None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and defs.fullmatch(node.name):
                start = _node_start(node)
                while start > 1 and lines[start - 2].lstrip().startswith("#"):
                    start -= 1
                cut(start, node.end_lineno)
                report["defs"].append(node.name)

    def inside_removed(node: ast.AST) -> bool:
        return node.lineno in removed

    if branch_tests is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.If) or inside_removed(node):
                continue
            if not lines[node.lineno - 1].lstrip().startswith("if"):
                continue  # an elif: handled with its chain head
            branches, tail = _chain(node, lines)
            hits = [b for b in branches if branch_tests.search(ast.get_source_segment(source, b.test) or "")]
            if not hits:
                continue
            kept = [b for b in branches if b not in hits]
            if not kept and tail:
                raise StripError(f"line {node.lineno}: every branch of a chain with an else would be removed")
            for b in hits:
                cut(b.lineno, b.body[-1].end_lineno)
                report["branches"].append(ast.get_source_segment(source, b.test))
            if not kept:
                cut(node.lineno, node.end_lineno)
            elif kept[0] is not node:
                head = lines[kept[0].lineno - 1]
                if not head.lstrip().startswith("elif"):
                    raise StripError(f"line {kept[0].lineno}: expected an elif")
                rewrites[kept[0].lineno] = head.replace("elif", "if", 1)

    if statements is not None:
        simple = (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Import, ast.ImportFrom)
        for node in ast.walk(tree):
            if isinstance(node, simple) and not inside_removed(node):
                if statements.search(ast.get_source_segment(source, node) or ""):
                    cut(node.lineno, node.end_lineno)
                    report["statements"].append(node.lineno)

    for pattern in drop_lines:
        hits = [i for i, text in enumerate(lines, 1) if pattern.search(text) and i not in removed]
        if not hits:
            raise StripError(f"drop-line pattern {pattern.pattern!r} matched nothing")
        removed.update(hits)
        report["lines"].extend(hits)

    # Blocks emptied by the cuts take their parent statement with them (repeat until stable).
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(node, ast.stmt) or not isinstance(body, list) or not body or inside_removed(node):
                continue
            if all(stmt.lineno in removed for stmt in body) and not getattr(node, "orelse", None):
                cut(_node_start(node), node.end_lineno)
                changed = True

    out = "".join(rewrites.get(i, text) for i, text in enumerate(lines, 1) if i not in removed)
    try:
        ast.parse(out)
    except SyntaxError as exc:
        raise StripError(f"stripped source does not parse: {exc}") from exc
    names = set(report["defs"])
    still = sorted({n.id for n in ast.walk(ast.parse(out)) if isinstance(n, ast.Name) and n.id in names})
    if still:
        raise StripError(f"removed definitions still referenced: {still}")
    return out, report


def compile_patterns(*patterns: str) -> Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))
