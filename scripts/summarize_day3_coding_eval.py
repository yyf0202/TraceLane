#!/usr/bin/env python3
"""Generate the descriptive Day 3 paired result report."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "artifacts/day3-coding-eval/results.json"
DEFAULT_REPORT = ROOT / "docs/experiments/2026-07-28-day3-results.md"


def _mean(values: list[float | int]) -> float:
    return statistics.fmean(values) if values else 0.0


def build_report(value: dict[str, object]) -> str:
    rows = value["attempts"]
    assert isinstance(rows, list)
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    indexed: dict[tuple[str, str, int, str], dict[str, object]] = {}
    for row in rows:
        assert isinstance(row, dict)
        grouped[(str(row["task"]), str(row["model"]), str(row["workflow"]))].append(row)
        indexed[
            (
                str(row["task"]),
                str(row["model"]),
                int(row["repeat"]),
                str(row["workflow"]),
            )
        ] = row

    lines = [
        "# Day 3 coding-eval matrix",
        "",
        "The 36 frozen slots use OpenCode H0 and run strictly serially through Ark.",
        "Provider failures remain reliability evidence rather than functional zeroes.",
        "All comparisons are paired and descriptive; this is one repository and not a",
        "statistical-significance or general model-ranking claim.",
        "",
        "## Attempt summary",
        "",
        "| Task | Model | Workflow | Scores | Full | Mean tokens | Mean seconds |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for key in sorted(grouped):
        task, model, workflow = key
        group = grouped[key]
        scores = "/".join(str(row["analysis_functional_score"]) for row in group)
        full = sum(
            row["analysis_functional_score"] == row["analysis_functional_possible"]
            for row in group
        )
        lines.append(
            f"| {task} | {model} | {workflow} | {scores} | {full}/2 "
            f"| {_mean([int(row['model_tokens']) for row in group]):.0f} "
            f"| {_mean([int(row['wall_ms']) for row in group]) / 1000:.1f} |"
        )

    pairs: list[dict[str, object]] = []
    for task in sorted({str(row["task"]) for row in rows}):
        for model in sorted({str(row["model"]) for row in rows}):
            for repeat in (1, 2):
                direct = indexed[(task, model, repeat, "direct-build")]
                plan = indexed[(task, model, repeat, "plan-build")]
                eligible = bool(
                    direct["capability_analysis_eligible"]
                    and plan["capability_analysis_eligible"]
                    and plan["build_started"]
                )
                pairs.append(
                    {
                        "task": task,
                        "model": model,
                        "repeat": repeat,
                        "eligible": eligible,
                        "direct": int(direct["analysis_functional_score"]),
                        "plan": int(plan["analysis_functional_score"]),
                        "score_delta": (
                            int(plan["analysis_functional_score"])
                            - int(direct["analysis_functional_score"])
                            if eligible
                            else None
                        ),
                        "token_delta": int(plan["model_tokens"])
                        - int(direct["model_tokens"]),
                        "wall_delta": int(plan["wall_ms"]) - int(direct["wall_ms"]),
                    }
                )

    lines.extend(
        [
            "",
            "## Matched pairs",
            "",
            "| Task | Model | Repeat | Direct | Plan | Score Δ | Token Δ | Seconds Δ |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for pair in pairs:
        score = f"{int(pair['score_delta']):+d}" if pair["eligible"] else "excluded"
        lines.append(
            f"| {pair['task']} | {pair['model']} | {pair['repeat']} "
            f"| {pair['direct']} | {pair['plan']} | {score} "
            f"| {int(pair['token_delta']):+d} "
            f"| {int(pair['wall_delta']) / 1000:+.1f} |"
        )

    eligible = [pair for pair in pairs if pair["eligible"]]
    deltas = [int(pair["score_delta"]) for pair in eligible]
    direct_scores = [int(pair["direct"]) for pair in eligible]
    plan_scores = [int(pair["plan"]) for pair in eligible]
    plan_rows = [row for row in rows if row["workflow"] == "plan-build"]
    gates = Counter(
        "pass" if row["plan_score"] == 100 else "fail"
        for row in plan_rows
        if row["capability_analysis_eligible"]
    )

    lines.extend(
        [
            "",
            "## Descriptive result",
            "",
            f"{len(eligible)}/{len(pairs)} pairs are capability-analysis eligible. "
            f"Direct averaged {_mean(direct_scores):.1f}; plan→build averaged "
            f"{_mean(plan_scores):.1f}; paired delta averaged {_mean(deltas):+.1f}. "
            f"Plan won {sum(delta > 0 for delta in deltas)}, tied "
            f"{sum(delta == 0 for delta in deltas)}, and lost "
            f"{sum(delta < 0 for delta in deltas)}.",
            "",
            f"Among provider-valid plan attempts, the frozen semantic gate passed "
            f"{gates['pass']} and blocked {gates['fail']}. A gate pass is build admission, "
            "not a prediction of full functional completion.",
            "",
            "BR-12 may later serve as a Meta-Harness task-level holdout. Its results must be",
            "withheld from the proposer during search even though they remain in the audited",
            "experiment store.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    value = json.loads(args.results.read_text(encoding="utf-8"))
    args.report.write_text(build_report(value), encoding="utf-8")
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
