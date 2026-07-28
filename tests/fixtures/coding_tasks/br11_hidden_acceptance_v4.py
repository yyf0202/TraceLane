"""Decoupled BR-11 functional-slice adjudicator.

V3 correctly removed prescribed helper names, but its pipeline scenario reused
the stricter discovery-role lookup.  A discovery-prefix failure could therefore
zero the unrelated pipeline slice.  V4 preserves the strict discovery check and
replaces only the pipeline slice with an independent call-graph check.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable
from pathlib import Path

import br11_hidden_acceptance_v3 as v3


def _role_name(
    functions: dict[str, str], *term_groups: tuple[str, ...]
) -> str:
    matches = []
    for name in functions:
        body = name + "\n" + v3._closure(name, functions)
        if all(any(term in body for term in group) for group in term_groups):
            matches.append((len(body), name))
    assert matches, term_groups
    return min(matches)[1]


def _call_lines(node: ast.AST, function_name: str) -> list[int]:
    return [
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == function_name
    ]


def _pipeline_check(repository: Path) -> None:
    path = repository / "scripts/scheduled_daily_run.py"
    source = path.read_text(encoding="utf-8")
    functions = v3._function_sources(source)
    main_source = functions["main"]
    main_tree = ast.parse(main_source)

    option_strings = {
        item for item in source.split('"') if item.startswith("--skip-")
    }
    assert any(
        "real" in item and ("sync" in item or "fill" in item)
        for item in option_strings
    )
    assert any(
        "real" in item and ("order" in item or "generate" in item)
        for item in option_strings
    )

    sync_name = _role_name(
        functions,
        ("sync_real_fills",),
        ("fill", "filled"),
        ("except",),
    )
    order_name = _role_name(
        functions,
        ("generate_order", "order_list"),
    )
    daily_lines = _call_lines(main_tree, "run_daily_pipeline")
    sync_lines = _call_lines(main_tree, sync_name)
    order_lines = _call_lines(main_tree, order_name)
    assert daily_lines and sync_lines and order_lines
    assert min(sync_lines) < min(daily_lines) < min(order_lines)

    pipeline_result_names = {
        target.id
        for node in ast.walk(main_tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "run_daily_pipeline"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert pipeline_result_names
    order_guarded = False
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.If):
            continue
        if not _call_lines(node, order_name):
            continue
        test = ast.get_source_segment(main_source, node.test) or ""
        if any(name in test for name in pipeline_result_names):
            order_guarded = True
            break
    assert order_guarded

    for role_name in (sync_name, order_name):
        role_tree = ast.parse(functions[role_name])
        role_isolated = (
            any(isinstance(node, ast.For) for node in ast.walk(role_tree))
            and any(isinstance(node, ast.Try) for node in ast.walk(role_tree))
            and any(
                isinstance(node, ast.ExceptHandler)
                for node in ast.walk(role_tree)
            )
        )
        main_isolated = (
            any(
                isinstance(node, ast.For) and _call_lines(node, role_name)
                for node in ast.walk(main_tree)
            )
            and any(
                isinstance(node, ast.Try) and _call_lines(node, role_name)
                for node in ast.walk(main_tree)
            )
        )
        assert role_isolated or main_isolated

    email_subjects = [
        ast.get_source_segment(main_source, call.args[1]) or ""
        for call in ast.walk(main_tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "send_email"
        and len(call.args) >= 2
    ]
    assert email_subjects
    assert "order_count" in main_source or any(
        "count" in subject or "subject" in subject
        for subject in email_subjects
    )


def _decoupled_score(
    task: str, checks: list[tuple[str, int, Callable[[], None]]]
) -> int:
    repository = _REPOSITORY
    replaced = [
        (
            name,
            points,
            (lambda: _pipeline_check(repository))
            if name == "pipeline_state_ordering_and_skip_guards"
            else check,
        )
        for name, points, check in checks
    ]
    return _ORIGINAL_SCORE(task, replaced)


def main(repository: Path) -> int:
    global _REPOSITORY
    _REPOSITORY = repository
    v3._score = _decoupled_score
    try:
        return v3.main(repository)
    finally:
        v3._score = _ORIGINAL_SCORE


_ORIGINAL_SCORE = v3._score
_REPOSITORY = Path()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
