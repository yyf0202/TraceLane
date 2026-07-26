"""Generate a controlled synthetic decision suite with known signal-to-noise.

Real research runs are a poor substrate for *ablation*: their ground truth is
unresolved, their sample size is tiny, and their noise is uncontrolled.  A
controlled generator fixes that — every knob that decides whether a harness
mechanism is load-bearing is explicit and the ground truth is known by
construction, so a debate or feedback ablation *must* surface its effect and
that effect is attributable to a named variable.

The generator builds a roster of analysts with a designed reliability profile:

* a block of **reliable** analysts whose direction matches the eventual world
  direction most of the time;
* a **noisy** analyst who is *confident but systematically wrong* — this is the
  single most important ingredient for the feedback loop, because the static
  arm keeps getting misled by that confidence while the self-improving arm
  learns to down-weight it;
* a tunable fraction of **high-disagreement** tasks, where the roster is split
  so the fused stance is genuinely contested — this is what gives the debate
  ablation discriminating power.

Determinism: everything is drawn from ``random.Random(seed)``; identical seeds
reproduce byte-identical suites.  Output is a directory of decision-suite task
files loadable by ``tracelane.spine.suite.load_decision_suite``.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path

_DIRECTIONS = ("bullish", "bearish")
_OPPOSITE = {"bullish": "bearish", "bearish": "bullish"}
_LICENSE = "CC0-1.0 synthetic (controlled ablation suite)"

# Analyst ids are fixed so reliability is *learnable across tasks*: the same
# noisy analyst keeps being wrong, which is exactly what the feedback loop keys
# on.  Reliable analysts are individually noisy but directionally dependable.
_RELIABLE_POOL = ("fund", "industry", "news", "flow")
_NOISY_ID = "noisy"


def _build_task(
    rng: random.Random,
    index: int,
    *,
    reliable_count: int,
    noise_confidence: float,
    high_disagreement: bool,
    reliability: float,
) -> dict[str, object]:
    task_id = f"SYN-{index:03d}"
    # The world's actual direction for this task.
    actual = rng.choice(_DIRECTIONS)

    reliable = list(_RELIABLE_POOL[:reliable_count])
    analysts: list[dict[str, object]] = []

    if high_disagreement:
        # Split the reliable block down the middle so conviction cancels; the
        # fused stance is genuinely contested regardless of the world.
        half = (len(reliable) + 1) // 2
        for position, analyst_id in enumerate(reliable):
            direction = actual if position < half else _OPPOSITE[actual]
            confidence = round(rng.uniform(0.7, 0.9), 3)
            analysts.append(
                {
                    "analyst_id": analyst_id,
                    "role": f"{analyst_id}-analyst",
                    "direction_hint": direction,
                    "confidence_hint": confidence,
                }
            )
    else:
        # Reliable block mostly agrees with the world.
        for analyst_id in reliable:
            correct = rng.random() < reliability
            direction = actual if correct else _OPPOSITE[actual]
            confidence = round(rng.uniform(0.6, 0.85), 3)
            analysts.append(
                {
                    "analyst_id": analyst_id,
                    "role": f"{analyst_id}-analyst",
                    "direction_hint": direction,
                    "confidence_hint": confidence,
                }
            )

    # The noisy analyst is confident and systematically wrong.
    analysts.append(
        {
            "analyst_id": _NOISY_ID,
            "role": "noisy-analyst",
            "direction_hint": _OPPOSITE[actual],
            "confidence_hint": noise_confidence,
        }
    )

    evidence = []
    expected_facts = {}
    for position, spec in enumerate(analysts, start=1):
        analyst_id = spec["analyst_id"]
        evidence.append(
            {
                "available_at": f"2026-01-{1 + position:02d}T09:00:00Z",
                "evidence_id": f"{task_id.lower()}-ev-{position:02d}",
                "fact_ids": [f"fact-{analyst_id}"],
                "source": f"tracelane-synthetic-{analyst_id}",
                "text": (
                    f"Synthetic {analyst_id} note for {task_id}: leans {spec['direction_hint']}."
                ),
            }
        )
        expected_facts[f"fact-{analyst_id}"] = f"Structural contribution of {analyst_id}."

    metric = 0.02 if actual == "bullish" else -0.02
    return {
        "analysts": analysts,
        "resolution": {
            "actual_direction": actual,
            "metric_name": "net_alpha",
            "metric_value": metric,
        },
        "completion_facts": [f"fact-{analysts[0]['analyst_id']}"],
        "cutoff_at": "2026-01-31T15:00:00Z",
        "evidence": evidence,
        "expected_facts": expected_facts,
        "fault_scenario": None,
        "future_evidence_ids": [],
        "license": _LICENSE,
        "question": f"Synthetic task {task_id}: reconcile the roster into a stance.",
        "task_id": task_id,
    }


def generate_suite(
    *,
    seed: int,
    task_count: int,
    reliable_count: int = 3,
    noise_confidence: float = 0.9,
    reliability: float = 0.8,
    disagreement_fraction: float = 0.3,
) -> list[dict[str, object]]:
    """Build a deterministic suite of decision-task documents."""
    if task_count < 1:
        raise ValueError("task_count must be a positive integer")
    if not (0.0 <= disagreement_fraction <= 1.0):
        raise ValueError("disagreement_fraction must be within [0, 1]")
    if not (0.0 <= reliability <= 1.0):
        raise ValueError("reliability must be within [0, 1]")
    if reliable_count < 1 or reliable_count > len(_RELIABLE_POOL):
        raise ValueError(f"reliable_count must be within [1, {len(_RELIABLE_POOL)}]")
    rng = random.Random(seed)
    tasks: list[dict[str, object]] = []
    for index in range(1, task_count + 1):
        high_disagreement = rng.random() < disagreement_fraction
        tasks.append(
            _build_task(
                rng,
                index,
                reliable_count=reliable_count,
                noise_confidence=noise_confidence,
                high_disagreement=high_disagreement,
                reliability=reliability,
            )
        )
    return tasks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output suite directory")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--tasks", type=int, default=12, help="Number of decision tasks")
    parser.add_argument("--reliable", type=int, default=3, help="Reliable analyst count (1-4)")
    parser.add_argument(
        "--noise-confidence",
        type=float,
        default=0.9,
        help="Confidence of the systematically-wrong noisy analyst",
    )
    parser.add_argument(
        "--reliability",
        type=float,
        default=0.8,
        help="Probability a reliable analyst matches the world direction",
    )
    parser.add_argument(
        "--disagreement-fraction",
        type=float,
        default=0.3,
        help="Fraction of tasks with a contested (high-disagreement) roster",
    )
    args = parser.parse_args(argv)

    tasks = generate_suite(
        seed=args.seed,
        task_count=args.tasks,
        reliable_count=args.reliable,
        noise_confidence=args.noise_confidence,
        reliability=args.reliability,
        disagreement_fraction=args.disagreement_fraction,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        target = args.out / f"{task['task_id'].lower()}.json"
        target.write_text(
            json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        f"wrote {len(tasks)} tasks to {args.out} "
        f"(seed={args.seed}, disagreement={args.disagreement_fraction}, "
        f"reliability={args.reliability}, noise_confidence={args.noise_confidence})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
