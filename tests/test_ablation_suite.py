from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tracelane.spine.experiments import ablate_feedback_loop
from tracelane.spine.suite import load_decision_suite

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_ablation_suite.py"


def generate(tmp_path: Path, seed: int = 7, tasks: int = 12, **kwargs: object) -> Path:
    out = tmp_path / "suite"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--out",
        str(out),
        "--seed",
        str(seed),
        "--tasks",
        str(tasks),
    ]
    for key, value in kwargs.items():
        cmd += [f"--{key.replace('_', '-')}", str(value)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return out


def test_generate_is_deterministic(tmp_path: Path) -> None:
    first = generate(tmp_path / "a")
    second = generate(tmp_path / "b")
    first_files = sorted(p.name for p in first.glob("*.json"))
    second_files = sorted(p.name for p in second.glob("*.json"))
    assert first_files == second_files
    for name in first_files:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_generate_produces_valid_suite(tmp_path: Path) -> None:
    suite = generate(tmp_path, tasks=8)
    specs = load_decision_suite(suite)
    assert len(specs) == 8
    for spec in specs:
        assert spec.resolution.actual_direction in ("bullish", "bearish")
        # Every task has the systematically-wrong noisy analyst.
        ids = [a.analyst_id for a in spec.analysts]
        assert "noisy" in ids


def test_noisy_analyst_is_systematically_wrong(tmp_path: Path) -> None:
    suite = generate(tmp_path, tasks=12, disagreement=0.0)
    specs = load_decision_suite(suite)
    for spec in specs:
        noisy = next(a for a in spec.analysts if a.analyst_id == "noisy")
        assert noisy.direction_hint != spec.resolution.actual_direction
        assert noisy.confidence_hint >= 0.8  # confident but wrong


def test_disagreement_fraction_splits_rosters(tmp_path: Path) -> None:
    suite = generate(tmp_path, tasks=20, disagreement=1.0)
    specs = load_decision_suite(suite)
    # With full disagreement, every task's reliable block is contested.
    for spec in specs:
        directions = [a.direction_hint for a in spec.analysts if a.analyst_id != "noisy"]
        assert "bullish" in directions and "bearish" in directions


def test_feedback_loop_improves_over_static(tmp_path: Path) -> None:
    # The generator's whole point: the self-improving arm must beat the static
    # arm once it learns to down-weight the noisy analyst.
    suite = generate(tmp_path, tasks=12, seed=7, disagreement=0.3)
    specs = load_decision_suite(suite)
    result = ablate_feedback_loop(specs, rounds=5, min_samples=3)
    static_final = result["arms"]["static"]["accuracy_per_round"][-1]
    improving_final = result["arms"]["self_improving"]["accuracy_per_round"][-1]
    assert improving_final > static_final
    reliability = result["arms"]["self_improving"]["final_reliability"]
    assert reliability["noisy"] < reliability.get("fund", 1.0)


def test_static_arm_is_flat(tmp_path: Path) -> None:
    suite = generate(tmp_path, tasks=12, seed=7)
    specs = load_decision_suite(suite)
    result = ablate_feedback_loop(specs, rounds=4, min_samples=3)
    static_acc = result["arms"]["static"]["accuracy_per_round"]
    assert static_acc == [static_acc[0]] * 4  # never learns
