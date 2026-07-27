#!/usr/bin/env python3
"""Generate the descriptive Day 2 cross-task result table."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "artifacts/day2-coding-eval/results.json"
REPORT = ROOT / "docs/experiments/2026-07-27-day2-results.md"


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def main() -> int:
    value = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = value["attempts"]
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(row["task"], row["model"], row["workflow"])].append(row)

    lines = [
        "# Day 2 complex-task matrix",
        "",
        "All 36 valid attempts ran strictly serially through Ark. Each cell has two",
        "repeats. These are descriptive paired results, not a statistical-significance",
        "claim, and they are not pooled with OpenCode Go.",
        "",
        "## Attempt summary",
        "",
        "| Task | Model | Workflow | Scores | Full | Normal finish | Mean tokens | Mean seconds |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for key in sorted(grouped):
        task, model, workflow = key
        group = grouped[key]
        scores = "/".join(str(row["functional_score"]) for row in group)
        full = sum(row["functional_score"] == row["functional_possible"] for row in group)
        finished = sum(row["end_reason"] == "completed" for row in group)
        lines.append(
            f"| {task} | {model} | {workflow} | {scores} | {full}/2 | {finished}/2 "
            f"| {_mean([row['model_tokens'] for row in group]):.0f} "
            f"| {_mean([row['wall_ms'] for row in group]) / 1000:.1f} |"
        )

    indexed = {(row["task"], row["model"], row["repeat"], row["workflow"]): row for row in rows}
    pairs: list[dict[str, object]] = []
    for task in sorted({row["task"] for row in rows}):
        for model in sorted({row["model"] for row in rows}):
            for repeat in (1, 2):
                direct = indexed[(task, model, repeat, "direct-build")]
                plan = indexed[(task, model, repeat, "plan-build")]
                pairs.append(
                    {
                        "task": task,
                        "model": model,
                        "repeat": repeat,
                        "score_delta": plan["functional_score"] - direct["functional_score"],
                        "token_delta": plan["model_tokens"] - direct["model_tokens"],
                        "wall_delta": plan["wall_ms"] - direct["wall_ms"],
                    }
                )

    lines.extend(
        [
            "",
            "## Paired deltas",
            "",
            "Positive score means plan→build completed more functional points. Positive",
            "resource values mean plan→build used more.",
            "",
            "| Task | Model | Repeat | Score Δ | Token Δ | Seconds Δ |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for pair in pairs:
        lines.append(
            f"| {pair['task']} | {pair['model']} | {pair['repeat']} "
            f"| {pair['score_delta']:+} | {pair['token_delta']:+} "
            f"| {pair['wall_delta'] / 1000:+.1f} |"
        )

    plan_rows = [row for row in rows if row["workflow"] == "plan-build"]
    gate_passed = [row for row in plan_rows if row["plan_score"] == 100]
    gate_full = [
        row for row in gate_passed if row["functional_score"] == row["functional_possible"]
    ]
    gate_failed = [row for row in plan_rows if row["plan_score"] != 100]
    direct_rows = [row for row in rows if row["workflow"] == "direct-build"]
    plan_avg = _mean([row["functional_score"] for row in plan_rows])
    direct_avg = _mean([row["functional_score"] for row in direct_rows])
    score_deltas = [pair["score_delta"] for pair in pairs]
    token_deltas = [pair["token_delta"] for pair in pairs]
    wall_deltas = [pair["wall_delta"] for pair in pairs]

    diagnosis_counts = Counter(phase["state"] for row in rows for phase in row["provider_turns"])
    local_termination_counts = Counter(
        phase["local_termination"]
        for row in rows
        for phase in row["provider_turns"]
        if phase.get("local_termination")
    )

    lines.extend(
        [
            "",
            "## Answers to the three questions",
            "",
            "### Did planning improve complex-feature completion?",
            "",
            f"Across the 18 matched pairs, plan→build averaged {plan_avg:.1f}/100 and "
            f"direct-build averaged {direct_avg:.1f}/100. The paired score delta averaged "
            f"{_mean(score_deltas):+.1f} points; plan won "
            f"{sum(delta > 0 for delta in score_deltas)} pairs, tied "
            f"{sum(delta == 0 for delta in score_deltas)}, and lost "
            f"{sum(delta < 0 for delta in score_deltas)}.",
            "",
            "### Did the plan gate predict build success?",
            "",
            f"{len(gate_passed)}/18 plans passed the semantic gate at 100/100. Of those, "
            f"{len(gate_full)} reached 100/100 functional completion. "
            f"{len(gate_failed)} plans failed the gate and did not start build. This is a "
            "descriptive calibration of this gate on these tasks, not a general predictive claim.",
            "",
            "### Did plan overhead buy more functional slices?",
            "",
            f"Plan→build used an average of {_mean(token_deltas):+.0f} tokens and "
            f"{_mean(wall_deltas) / 1000:+.1f} seconds per pair relative to direct-build, "
            f"for the {_mean(score_deltas):+.1f}-point mean functional delta above. "
            "The paired table shows where extra resources did and did not buy slices.",
            "",
            "## Provider lifecycle",
            "",
            "Last-turn states across imported phases: "
            + ", ".join(f"`{key}` {count}" for key, count in sorted(diagnosis_counts.items()))
            + ".",
            "",
            "Local budget terminations: "
            + (
                ", ".join(
                    f"`{key}` {count}" for key, count in sorted(local_termination_counts.items())
                )
                if local_termination_counts
                else "none"
            )
            + ". No failed attempt was automatically rerun.",
            "",
            "## Scope",
            "",
            "The matrix contains three tasks, three Ark models, two workflows, and two",
            "repeats. It can reveal concrete cross-task patterns and gate failures. It is",
            "too small and too repository-specific to support a statistical or universal",
            "claim about planning.",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
