"""Independent structural acceptance check for historical BeRicher task BR-03."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def source(repository: Path, relative: str) -> str:
    return (repository / relative).read_text(encoding="utf-8")


def calls_named(tree: ast.AST, name: str) -> int:
    return sum(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(tree)
    )


def integer_expression(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp):
        left = integer_expression(node.left)
        right = integer_expression(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
    return None


if __name__ == "__main__":
    repository = Path(sys.argv[1])
    compact = source(repository, "scripts/compact_incremental.py")
    daily = source(repository, "src/cli/daily_run.py")
    update = source(repository, "src/cli/update_data.py")
    scheduled = source(repository, "scripts/scheduled_daily_run.py")

    compact_tree = ast.parse(compact)
    function_names = {node.name for node in compact_tree.body if isinstance(node, ast.FunctionDef)}
    assert "maybe_compact" in function_names, "missing auto-compaction entry point"
    assert "30" in compact and "7" in compact, "threshold and weekly fallback must be explicit"

    assert calls_named(ast.parse(daily), "maybe_compact") >= 1
    assert calls_named(ast.parse(update), "maybe_compact") >= 1
    assert "RotatingFileHandler" in daily
    assert "maxBytes" in daily and "backupCount" in daily

    scheduled_tree = ast.parse(scheduled)
    timeout_values = [
        integer_expression(keyword.value)
        for node in ast.walk(scheduled_tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "timeout"
    ]
    assert any(value is not None and value >= 14_400 for value in timeout_values), timeout_values
    print("BR-03 independent acceptance passed")
