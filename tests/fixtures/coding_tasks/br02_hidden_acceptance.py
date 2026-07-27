"""Independent acceptance check for the BR-02 coding-task pilot."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def load_helper(repository: Path):
    path = repository / "src/cli/daily_run.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    helper = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {"_training_info_uses_expanded_static", "_detect_expanded_static", "_is_expanded_static"}
    )
    expanded = {"pb", "revenue_yoy", "ocfps", "current_ratio"}
    namespace: dict[str, object] = {
        "EXPANDED_STATIC_FEATURES": expanded,
        "_EXPANDED_STATIC_COLS": expanded,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[helper.name]


if __name__ == "__main__":
    helper = load_helper(Path(sys.argv[1]))
    assert helper(
        {
            "extra": {"static_feature_cols": []},
            "data": {"feature_cols": ["close", "pb", "revenue_yoy"]},
        }
    )
    assert helper({"extra": {"static_feature_cols": ["a", "b", "c"]}, "data": {"feature_cols": []}})
    assert not helper(
        {
            "extra": {"static_feature_cols": ["a", "b"]},
            "data": {"feature_cols": ["close", "volume"]},
        }
    )
    print("BR-02 independent acceptance passed")
