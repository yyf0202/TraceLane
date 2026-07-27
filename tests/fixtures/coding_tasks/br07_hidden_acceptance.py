"""Functional-slice acceptance for BR-07 FactorVAE crash resilience."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    sys.path.insert(0, str(repository))
    train_path = repository / "src/cli/factorvae_cs_train.py"
    train_source = train_path.read_text(encoding="utf-8")

    def distinct_factor_bound() -> None:
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
        assert model.prior_head.logvar_max == 1.25
        assert model.posterior_head.logvar_max == 1.25
        assert model.logvar_max == 9.0

    def config_propagation() -> None:
        tree = ast.parse(train_source)
        names = {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
        assert "factor_logvar_max" in names
        assert '"factor_logvar_max": self.factor_logvar_max' in train_source
        assert "factor_logvar_max=args.factor_logvar_max" in train_source

    def cli_contract() -> None:
        assert '--factor-logvar-max"' in train_source
        assert "default=2.0" in train_source[train_source.index('--factor-logvar-max"') :][:220]

    def epoch_checkpoint() -> None:
        start = train_source.index("def train_one_fold")
        end = train_source.index("\ndef ", start + 10)
        body = train_source[start:end]
        loop = body.index("for epoch")
        save = body.index("_save_fold", loop)
        assert save > loop
        assert "except Exception" in body[save : save + 900]
        assert "warning" in body[save : save + 900]

    def early_partial_metadata() -> None:
        meta = train_source.index('"kfold_meta.json"')
        fold_loop = train_source.index("for fold_idx in folds_to_run")
        assert meta < fold_loop
        assert '"partial": True' in train_source[meta:fold_loop]

    checks = [
        ("factor_head_uses_distinct_bound", 30, distinct_factor_bound),
        ("config_and_model_propagation", 20, config_propagation),
        ("cli_default_contract", 10, cli_contract),
        ("checkpoint_each_epoch_nonfatal", 25, epoch_checkpoint),
        ("partial_metadata_before_training", 15, early_partial_metadata),
    ]
    return _score("BR-07", checks)


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
