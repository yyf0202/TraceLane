from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADER = ROOT / "tests/fixtures/coding_tasks/br05_v3_plan_acceptance.py"


def _grade(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GRADER), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_br05_v3_plan_gate_accepts_true_stepwise_film_plan(tmp_path: Path) -> None:
    result = _grade(
        tmp_path,
        """
        - In src/backtest/engine.py keep the previous signal so T executes at T+1;
          skip the first day and make no trade when there is no prior signal.
        - In src/components/target_generator.py update crosssectional ranking and
          OHLC labels to the T+1 -> T+2 window with shift(-2).
        - In src/components/models.py implement FiLM per-timestep. Keep
          x_static = x[:, :, -n_static:] as [B, T, n_static], generate gamma and
          beta as [B, T, d_model], and apply them to the matching dynamic timestep.
          Do not collapse the time dimension. Use sequence mean mean(dim=1) only
          for the separate Bottleneck context token.
        - Validation: run tests, python3 -m py_compile, and git diff --check.
        """,
    )

    assert result.returncode == 0
    assert '"earned": 100' in result.stdout


def test_br05_v3_plan_gate_rejects_r5_mean_broadcast_false_positive(tmp_path: Path) -> None:
    result = _grade(
        tmp_path,
        """
        - In src/backtest/engine.py keep the previous signal so T executes at T+1;
          skip the first day and make no trade when there is no prior signal.
        - In src/components/target_generator.py update crosssectional ranking and
          OHLC labels to the T+1 -> T+2 window with shift(-2).
        - In src/components/models.py make FiLM per-timestep by setting
          x_static = x[:, :, -n_static:].mean(dim=1). Generate gamma and beta as
          [B, 1, d_model], then broadcast them across dynamic_emb; this preserves
          per-timestep modulation. Use sequence mean mean(dim=1) for the separate
          Bottleneck context token.
        - Validation: run tests, python3 -m py_compile, and git diff --check.
        """,
    )

    assert result.returncode == 1
    score = json.loads(result.stdout.removeprefix("TRACELANE_PLAN_SCORE="))
    film = next(item for item in score["criteria"] if item["name"] == "film_stepwise_static")
    assert film["earned"] == 0
    assert score["earned"] == 85


def test_br05_v3_plan_gate_rejects_correct_words_without_time_shapes(tmp_path: Path) -> None:
    result = _grade(
        tmp_path,
        """
        - In src/backtest/engine.py keep the previous signal so T executes at T+1;
          skip the first day and make no trade when there is no prior signal.
        - In src/components/target_generator.py update crosssectional ranking and
          OHLC labels to the T+1 -> T+2 window with shift(-2).
        - In src/components/models.py make FiLM per-timestep with gamma and beta.
          Use sequence mean mean(dim=1) for the separate Bottleneck context token.
        - Validation: run tests, python3 -m py_compile, and git diff --check.
        """,
    )

    assert result.returncode == 1
    assert '"earned": 85' in result.stdout
