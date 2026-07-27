"""Independent structural acceptance check for historical BeRicher task BR-04."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def tree(repository: Path, relative: str) -> ast.AST:
    return ast.parse((repository / relative).read_text(encoding="utf-8"))


def calls(tree_value: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree_value)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


if __name__ == "__main__":
    repository = Path(sys.argv[1])
    daily_run = tree(repository, "src/cli/daily_run.py")
    daily_runner = tree(repository, "src/paper_trading/daily_runner.py")

    pipeline_calls = calls(daily_run, "build_feature_pipeline") + calls(
        daily_runner, "build_feature_pipeline"
    )
    expanded_calls = [
        call
        for call in pipeline_calls
        if any(keyword.arg == "expanded_static" for keyword in call.keywords)
    ]
    assert len(expanded_calls) >= 3, len(expanded_calls)

    cache_key_assignments = [
        node.value
        for node in ast.walk(daily_run)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and "cache_key" in target.id for target in node.targets
        )
        and isinstance(node.value, ast.Tuple)
    ]
    assert any(len(value.elts) >= 2 for value in cache_key_assignments), (
        "feature cache must distinguish version and expanded-static mode"
    )

    runner_source = (repository / "src/paper_trading/daily_runner.py").read_text(encoding="utf-8")
    assert "static_feature_cols" in runner_source
    print("BR-04 independent acceptance passed")
