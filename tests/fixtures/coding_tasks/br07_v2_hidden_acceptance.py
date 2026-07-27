"""Behavior-oriented acceptance for BR-07 FactorVAE crash resilience."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _calls_name(node: ast.AST, fragments: tuple[str, ...]) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if any(fragment in name.lower() for fragment in fragments):
            return True
    return False


def main(repository: Path) -> int:
    sys.path.insert(0, str(repository))
    train_path = repository / "src/cli/factorvae_cs_train.py"
    train_source = train_path.read_text(encoding="utf-8")
    train_tree = ast.parse(train_source)

    def distinct_factor_bound_behavior() -> None:
        import torch
        from src.components.models import FactorVAECrossSectionalModel

        model = FactorVAECrossSectionalModel(
            input_dim=4,
            output_dim=1,
            n_static_features=1,
            d_model=8,
            nhead=2,
            num_layers=1,
            dim_feedforward=16,
            z_dim=2,
            n_portfolios=2,
            factor_hidden_dim=8,
            factor_logvar_max=1.25,
            logvar_max=9.0,
        )
        with torch.no_grad():
            for head in (model.prior_head, model.posterior_head):
                output = head.to_factor[-1]
                output.weight.zero_()
                output.bias[: model.z_dim].zero_()
                output.bias[model.z_dim :].fill_(50.0)

        embedding = torch.zeros(3, model.d_model)
        target = torch.zeros(3, 1)
        _, prior_logvar = model.prior_head(embedding)
        _, posterior_logvar = model.posterior_head(embedding, target)
        assert torch.all(prior_logvar <= 1.25)
        assert torch.all(posterior_logvar <= 1.25)
        assert model.logvar_max == 9.0

    def config_and_cli_behavior() -> None:
        from src.cli.factorvae_cs_train import CSTrainConfig, build_parser

        cfg = CSTrainConfig(input_dim=4, n_static_features=1, factor_logvar_max=1.25)
        assert cfg.model_init_kwargs()["factor_logvar_max"] == 1.25
        parser = build_parser()
        assert parser.parse_args([]).factor_logvar_max == 2.0
        assert parser.parse_args(["--factor-logvar-max", "1.75"]).factor_logvar_max == 1.75

    def checkpoint_inside_epoch_is_nonfatal() -> None:
        function = _function(train_tree, "train_one_fold")
        epoch_loops = [
            node
            for node in ast.walk(function)
            if isinstance(node, (ast.For, ast.While))
            and (
                isinstance(getattr(node, "target", None), ast.Name)
                and node.target.id == "epoch"
                or "epoch" in ast.unparse(node).splitlines()[0].lower()
            )
        ]
        assert epoch_loops
        epoch_loop = epoch_loops[0]
        checkpoint_tries = [
            node
            for node in ast.walk(epoch_loop)
            if isinstance(node, ast.Try)
            and _calls_name(node, ("save", "checkpoint"))
            and any(
                _calls_name(handler, ("warning", "warn"))
                for handler in node.handlers
            )
        ]
        assert checkpoint_tries
        assert any(
            isinstance(handler.type, ast.Name)
            and handler.type.id in {"Exception", "OSError", "RuntimeError"}
            or isinstance(handler.type, ast.Tuple)
            for node in checkpoint_tries
            for handler in node.handlers
        )

    def partial_metadata_precedes_fold_training() -> None:
        function = _function(train_tree, "cmd_factorvae_cs_train")
        fold_loops = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "fold_idx"
        ]
        assert fold_loops
        first_fold_line = min(node.lineno for node in fold_loops)

        metadata_writes = []
        for node in ast.walk(function):
            if not isinstance(node, (ast.With, ast.Assign, ast.Expr)):
                continue
            segment = ast.get_source_segment(train_source, node) or ""
            if "kfold_meta.json" in segment and (
                "json.dump" in segment or "write" in segment or "write_text" in segment
            ):
                metadata_writes.append(node)
        assert metadata_writes
        assert min(node.lineno for node in metadata_writes) < first_fold_line

        prefix = "\n".join(train_source.splitlines()[: first_fold_line - 1]).lower()
        assert "partial" in prefix and "true" in prefix

    checks = [
        ("factor_heads_enforce_distinct_bound", 30, distinct_factor_bound_behavior),
        ("config_and_cli_round_trip", 30, config_and_cli_behavior),
        ("checkpoint_each_epoch_nonfatal", 25, checkpoint_inside_epoch_is_nonfatal),
        ("partial_metadata_before_training", 15, partial_metadata_precedes_fold_training),
    ]
    return _score("BR-07 v2", checks)


def _score(task: str, checks: list[tuple[str, int, Callable[[], None]]]) -> int:
    outcomes, earned = [], 0
    for name, points, check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            earned += points
            outcomes.append({"name": name, "points": points, "earned": points})
    print(
        "TRACELANE_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
