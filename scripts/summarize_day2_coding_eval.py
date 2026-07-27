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
                pairs.append(
                    {
                        "task": task,
                        "model": model,
                        "repeat": repeat,
                        "eligible": eligible,
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
            else f"| {pair['task']} | {pair['model']} | {pair['repeat']} | excluded"
        )
        lines[-1] += (
            f" | {pair['token_delta']:+} "
            f"| {pair['wall_delta'] / 1000:+.1f} |"
        )

    eligible_rows = [row for row in rows if row["capability_analysis_eligible"]]
    plan_rows = [row for row in eligible_rows if row["workflow"] == "plan-build"]
    gate_passed = [row for row in plan_rows if row["plan_score"] == 100]
    gate_full = [
        row
        for row in gate_passed
        if row["analysis_functional_score"] == row["analysis_functional_possible"]
    ]
    gate_failed = [row for row in plan_rows if row["plan_score"] != 100]
    direct_rows = [row for row in eligible_rows if row["workflow"] == "direct-build"]
    plan_avg = _mean([row["analysis_functional_score"] for row in plan_rows])
    direct_avg = _mean([row["analysis_functional_score"] for row in direct_rows])
    eligible_pairs = [pair for pair in pairs if pair["eligible"]]
    score_deltas = [pair["score_delta"] for pair in eligible_pairs]
    token_deltas = [pair["token_delta"] for pair in eligible_pairs]
    wall_deltas = [pair["wall_delta"] for pair in eligible_pairs]

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
            f"Across {len(eligible_pairs)} quota-eligible matched pairs, plan→build "
            f"averaged {plan_avg:.1f}/100 and "
            f"direct-build averaged {direct_avg:.1f}/100. The paired score delta averaged "
            f"{_mean(score_deltas):+.1f} points; plan won "
            f"{sum(delta > 0 for delta in score_deltas)} pairs, tied "
            f"{sum(delta == 0 for delta in score_deltas)}, and lost "
            f"{sum(delta < 0 for delta in score_deltas)}.",
            "",
            "### Did the plan gate predict build success?",
            "",
            f"{len(gate_passed)}/{len(plan_rows)} quota-eligible plans passed the semantic "
            "gate at 100/100. Of those, "
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
            "The preregistered matrix contains three tasks, three Ark models, two workflows,",
            "and two repeats. Asterisks in the score table mark BR-07/08 v2 adjudication.",
            "Quota-rejected attempts are retained as provider evidence but excluded from",
            "capability deltas. The matrix can reveal concrete cross-task patterns and gate",
            "failures; it is too small and repository-specific for a statistical or",
            "universal claim about planning.",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
