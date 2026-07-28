"""Functional-slice acceptance for BR-12 FactorVAE k-fold warmup orchestration."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    training_path = repository / "src/cli/kfold_train.py"
    training = training_path.read_text(encoding="utf-8")
    shell_path = repository / "scripts/train_factorvae_phase1.sh"
    batch_path = repository / "scripts/train_factorvae_phase1.bat"
    shell = shell_path.read_text(encoding="utf-8") if shell_path.exists() else ""
    batch = batch_path.read_text(encoding="utf-8") if batch_path.exists() else ""

    def warmup_advances_inside_epoch_loop() -> None:
        tree = ast.parse(training)
        loops = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "epoch"
        ]
        matching = []
        for loop in loops:
            body = ast.get_source_segment(training, loop) or ""
            if ".set_epoch(" in body and "args.epochs" in body:
                matching.append(body)
        assert matching
        body = matching[0]
        assert body.index(".set_epoch(") < body.index("run_epoch")

    def recipes_match_across_platforms() -> None:
        required = (
            "--model factor_vae",
            "--feature-version v2",
            "--static-features expanded",
            "--k-folds 5",
            "--purge-left 3",
            "--purge-right 65",
            "--batch-size 4096",
            "--z-dim 8",
        )
        for term in required:
            assert term in shell, term
            assert term in batch.replace("^", ""), term

    def main_and_inert_control_arms() -> None:
        for source in (shell, batch):
            assert "--kl-beta 0.5" in source
            assert "--lambda-recon 1.0" in source
            assert "--kl-warmup-epochs 10" in source
            assert "--prior-warmup-epochs 5" in source
            assert "--kl-beta 0" in source
            assert "--lambda-recon 0" in source
            assert "--main-only" in source

    def failure_stops_later_arm() -> None:
        assert "set -e" in shell
        assert "if errorlevel 1 goto :error" in batch
        assert shell.index("--main-only") < shell.rindex("--kl-beta 0")
        assert batch.index("--main-only") < batch.rindex("--kl-beta 0")

    return _score(
        "BR-12",
        [
            ("set_epoch_before_each_kfold_training_epoch", 50, warmup_advances_inside_epoch_loop),
            ("cross_platform_recipe_parity", 20, recipes_match_across_platforms),
            ("main_and_prior_only_control_arms", 20, main_and_inert_control_arms),
            ("failure_and_main_only_control_flow", 10, failure_stops_later_arm),
        ],
    )


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
