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
        "The 36 preregistered attempt slots ran strictly serially through Ark. Attempts",
        "rejected by Ark's account-quota window remain in the reliability record but are",
        "excluded from capability deltas. BR-07/08 analysis scores use frozen v2",
        "adjudications while retaining the original v1 scores. These are descriptive paired",
        "results, not a statistical-significance claim, and they are not pooled with",
        "OpenCode Go.",
        "",
        "## Attempt summary",
        "",
        "| Task | Model | Workflow | Scores | Full | Normal finish | Mean tokens | Mean seconds |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for key in sorted(grouped):
        task, model, workflow = key
        group = grouped[key]
        scores = "/".join(
            str(row["analysis_functional_score"])
            + ("*" if row["adjudicated_functional_score"] is not None else "")
            for row in group
        )
        full = sum(
            row["analysis_functional_score"] == row["analysis_functional_possible"]
            for row in group
        )
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
                eligible = (
                    direct["capability_analysis_eligible"]
                    and plan["capability_analysis_eligible"]
                )
                false_gate_block = (
                    plan["analysis_plan_score"] == 100
                    and plan["plan_score"] != 100
                    and not plan["build_started"]
                )
                exclusion = (
                    "quota"
                    if not eligible
                    else "frozen-gate false block"
                    if false_gate_block
                    else None
                )
                eligible = eligible and not false_gate_block
                pairs.append(
                    {
                        "task": task,
                        "model": model,
                        "repeat": repeat,
                        "eligible": eligible,
                        "exclusion": exclusion,
                        "direct_score": direct["analysis_functional_score"],
                        "plan_score": plan["analysis_functional_score"],
                        "score_delta": (
                            plan["analysis_functional_score"]
                            - direct["analysis_functional_score"]
                            if eligible
                            else None
                        ),
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
            f"| {pair['score_delta']:+}"
            if pair["eligible"]
            else (
                f"| {pair['task']} | {pair['model']} | {pair['repeat']} "
                f"| excluded ({pair['exclusion']})"
            )
        )
        lines[-1] += (
            f" | {pair['token_delta']:+} "
            f"| {pair['wall_delta'] / 1000:+.1f} |"
        )

    eligible_rows = [row for row in rows if row["capability_analysis_eligible"]]
    plan_rows = [row for row in eligible_rows if row["workflow"] == "plan-build"]
    frozen_gate_passed = [row for row in plan_rows if row["plan_score"] == 100]
    gate_passed = [row for row in plan_rows if row["analysis_plan_score"] == 100]
    gate_passed_with_build = [row for row in gate_passed if row["build_started"]]
    gate_full = [
        row
        for row in gate_passed_with_build
        if row["analysis_functional_score"] == row["analysis_functional_possible"]
    ]
    gate_false_blocks = [
        row
        for row in gate_passed
        if row["plan_score"] != 100 and not row["build_started"]
    ]
    gate_failed = [row for row in plan_rows if row["analysis_plan_score"] != 100]
    eligible_pairs = [pair for pair in pairs if pair["eligible"]]
    plan_avg = _mean([pair["plan_score"] for pair in eligible_pairs])
    direct_avg = _mean([pair["direct_score"] for pair in eligible_pairs])
    score_deltas = [pair["score_delta"] for pair in eligible_pairs]
    token_deltas = [pair["token_delta"] for pair in eligible_pairs]
    wall_deltas = [pair["wall_delta"] for pair in eligible_pairs]

    lines.extend(
        [
            "",
            "## Model view",
            "",
            "| Model | Eligible pairs | Direct mean | Plan mean | Mean Δ | W/T/L | "
            "Quota-rejected slots |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model in sorted({row["model"] for row in rows}):
        model_pairs = [pair for pair in eligible_pairs if pair["model"] == model]
        model_deltas = [pair["score_delta"] for pair in model_pairs]
        rejected = sum(
            not row["capability_analysis_eligible"]
            for row in rows
            if row["model"] == model
        )
        lines.append(
            f"| {model} | {len(model_pairs)} "
            f"| {_mean([pair['direct_score'] for pair in model_pairs]):.1f} "
            f"| {_mean([pair['plan_score'] for pair in model_pairs]):.1f} "
            f"| {_mean(model_deltas):+.1f} "
            f"| {sum(delta > 0 for delta in model_deltas)}/"
            f"{sum(delta == 0 for delta in model_deltas)}/"
            f"{sum(delta < 0 for delta in model_deltas)} | {rejected} |"
        )
    lines.extend(
        [
            "",
            "Eligible-pair composition differs by model because quota rejection and frozen",
            "gate false blocks are excluded. This table describes observed model/workflow",
            "outcomes; it is not a controlled ranking of the three models.",
        ]
    )

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
            f"Across {len(eligible_pairs)} analysis-eligible matched pairs, plan→build "
            f"averaged {plan_avg:.1f}/100 and "
            f"direct-build averaged {direct_avg:.1f}/100. The paired score delta averaged "
            f"{_mean(score_deltas):+.1f} points; plan won "
            f"{sum(delta > 0 for delta in score_deltas)} pairs, tied "
            f"{sum(delta == 0 for delta in score_deltas)}, and lost "
            f"{sum(delta < 0 for delta in score_deltas)}.",
            "",
            "### Did the plan gate predict build success?",
            "",
            f"The frozen gate passed {len(frozen_gate_passed)}/{len(plan_rows)} "
            f"quota-eligible plans; versioned adjudication passes {len(gate_passed)}. "
            f"{len(gate_false_blocks)} adjudicated passes were blocked by the frozen gate, "
            "so no build outcome exists for them. Of the "
            f"{len(gate_passed_with_build)} adjudicated passes that actually built, "
            f"{len(gate_full)} reached 100/100 functional completion. "
            f"{len(gate_failed)} plans still fail after adjudication. This is descriptive "
            "gate calibration, not a general predictive claim.",
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
            "The preregistered matrix contains three tasks, three Ark models, two workflows,",
            "and two repeats. Asterisks in the score table mark BR-07/08 v2 adjudication.",
            "Quota-rejected attempts are retained as provider evidence but excluded from",
            "capability deltas; frozen-gate false blocks are retained for gate calibration",
            "but excluded from build-completion deltas because no build outcome exists.",
            "The matrix can reveal concrete cross-task patterns and gate failures; it is too",
            "small and repository-specific for a statistical or universal claim about planning.",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
