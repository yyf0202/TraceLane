#!/usr/bin/env python3
"""Build one Day 2 inventory without pooling preregistered, recovery, or replay layers."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "artifacts/day2-coding-eval/results.json"
RECOVERY1 = ROOT / "artifacts/day2-recovery/results.json"
RECOVERY2 = ROOT / "artifacts/day2-recovery2/results.json"
REPORT = ROOT / "docs/experiments/2026-07-28-day2-consolidated.md"


def _mean(values: list[int]) -> float:
    return statistics.fmean(values) if values else 0.0


def build_report(
    original: dict[str, object],
    recovery1: dict[str, object],
    recovery2: dict[str, object],
) -> str:
    original_rows = original["attempts"]
    recovery1_rows = recovery1["layers"]["quota_recovery_complete_pairs"]
    replay_rows = recovery1["layers"]["corrected_gate_build_replays"]
    recovery2_rows = recovery2["layers"]["quota_recovery_complete_pairs"]
    usable_original = [
        row for row in original_rows if row["capability_analysis_eligible"]
    ]
    failed_recovery1 = [
        row for row in recovery1_rows if not row["capability_analysis_eligible"]
    ]
    paired_recovery2: list[tuple[dict[str, object], dict[str, object]]] = []
    by_pair: dict[tuple[str, int], dict[str, dict[str, object]]] = {}
    for row in recovery2_rows:
        by_pair.setdefault((row["model"], row["repeat"]), {})[row["workflow"]] = row
    for workflows in by_pair.values():
        paired_recovery2.append(
            (workflows["direct-build"], workflows["plan-build"])
        )
    recovery2_deltas = [
        plan["analysis_functional_score"] - direct["analysis_functional_score"]
        for direct, plan in paired_recovery2
    ]
    recovery2_direct_mean = _mean(
        [direct["analysis_functional_score"] for direct, _ in paired_recovery2]
    )
    recovery2_plan_mean = _mean(
        [plan["analysis_functional_score"] for _, plan in paired_recovery2]
    )
    lines = [
        "# Day 2 layered evidence inventory",
        "",
        "This document indexes the Day 2 evidence without rewriting or pooling its layers.",
        "All comparisons are descriptive and repository-specific.",
        "",
        "## Layer inventory",
        "",
        "| Layer | Attempts | Provider-valid records | Purpose |",
        "|---|---:|---:|---|",
        f"| Original preregistered matrix | {len(original_rows)} | "
        f"{len(usable_original)} slots | Primary Day 2 matrix |",
        f"| Recovery1 | {len(recovery1_rows)} | "
        f"{len(recovery1_rows) - len(failed_recovery1)} attempts | Ark failure record |",
        f"| Corrected-gate replay | {len(replay_rows)} | {len(replay_rows)} builds "
        "| Diagnose frozen-gate false blocks |",
        f"| BR-07 recovery2 | {len(recovery2_rows)} | {len(recovery2_rows)} attempts "
        "| Three replacement pairs with stable transport |",
        "",
        "## Recovery2 matched pairs",
        "",
        "| Model | Repeat | Direct | Plan→build | Δ | Direct tokens | Plan tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for direct, plan in sorted(
        paired_recovery2,
        key=lambda pair: (pair[0]["model"], pair[0]["repeat"]),
    ):
        delta = plan["analysis_functional_score"] - direct["analysis_functional_score"]
        lines.append(
            f"| {direct['model']} | {direct['repeat']} "
            f"| {direct['analysis_functional_score']} "
            f"| {plan['analysis_functional_score']} | {delta:+} "
            f"| {direct['model_tokens']} | {plan['model_tokens']} |"
        )
    lines.extend(
        [
            "",
            f"Recovery2 direct averaged {recovery2_direct_mean:.1f}; "
            f"plan→build averaged {recovery2_plan_mean:.1f}; "
            f"mean paired delta {_mean(recovery2_deltas):+.1f}. "
            f"Plan won {sum(delta > 0 for delta in recovery2_deltas)}, tied "
            f"{sum(delta == 0 for delta in recovery2_deltas)}, and lost "
            f"{sum(delta < 0 for delta in recovery2_deltas)}.",
            "",
            "## Gate replay",
            "",
            "| Task | Model | Frozen gate | Corrected gate | Function | End |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in replay_rows:
        lines.append(
            f"| {row['task']} | {row['model']} | {row['frozen_plan_score']} "
            f"| {row['corrected_plan_score']} | {row['analysis_functional_score']} "
            f"| {row['execution_reason']} |"
        )
    lines.extend(
        [
            "",
            "The corrected gate allowed all six frozen plans to build and recover 30–80",
            "functional points. This establishes frozen-gate false negatives; it does not",
            "make a 100/100 plan score a predictor of full implementation.",
            "",
            "## Interpretation boundary",
            "",
            "- Original matrix estimates remain exactly as originally reported.",
            "- Recovery1 transport failures are not functional zeroes.",
            "- Recovery2 replaces missing evidence only within its three explicitly labeled pairs.",
            "- Gate replays are counterfactual build diagnostics, not preregistered matrix rows.",
            "- Ark and OpenCode Go remain separate provider strata.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    values = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (ORIGINAL, RECOVERY1, RECOVERY2)
    ]
    REPORT.write_text(build_report(*values), encoding="utf-8")
    print(REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
