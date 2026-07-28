#!/usr/bin/env python3
"""Run the Day 2 quota-recovery pairs and corrected-gate build replays serially."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import run_day2_coding_eval as experiment

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[1]
PLAN_GATE_V3 = ROOT / "tests/fixtures/coding_tasks/day2_v3_plan_acceptance.py"
BR07_V2 = experiment.TaskSpec(
    "BR-07",
    ROOT / "fixtures/coding/bericher-v0.7/tasks/BR-07-factorvae-crash-resilience-v2.json",
    ROOT / "tests/fixtures/coding_tasks/br07_v2_hidden_acceptance.py",
    "v2",
)
BR08_V2_GRADER = ROOT / "tests/fixtures/coding_tasks/br08_v2_hidden_acceptance.py"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recovery_matrix() -> list[experiment.AttemptSpec]:
    return [
        experiment.AttemptSpec(
            BR07_V2,
            "kimi-k2.7-code",
            2,
            "plan-build",
            "recovery1",
        ),
        experiment.AttemptSpec(
            BR07_V2,
            "kimi-k2.7-code",
            2,
            "direct-build",
            "recovery1",
        ),
        experiment.AttemptSpec(BR07_V2, "glm-5.2", 1, "plan-build", "recovery1"),
        experiment.AttemptSpec(BR07_V2, "glm-5.2", 1, "direct-build", "recovery1"),
        experiment.AttemptSpec(BR07_V2, "glm-5.2", 2, "direct-build", "recovery1"),
        experiment.AttemptSpec(BR07_V2, "glm-5.2", 2, "plan-build", "recovery1"),
    ]


@dataclass(frozen=True)
class ReplaySpec:
    source: experiment.AttemptSpec
    gate_version: int
    adjudicated_grader: Path

    @property
    def run_spec(self) -> experiment.AttemptSpec:
        return experiment.AttemptSpec(
            self.source.task,
            self.source.model,
            self.source.repeat,
            self.source.workflow,
            f"gate-replay-v{self.gate_version}",
        )


def _original(task: str, model: str, repeat: int) -> experiment.AttemptSpec:
    for spec in experiment.matrix():
        if (
            spec.task.short_id == task
            and spec.model == model
            and spec.repeat == repeat
            and spec.workflow == "plan-build"
        ):
            return spec
    raise ValueError(f"missing original attempt: {task} {model} r{repeat}")


def replay_matrix() -> list[ReplaySpec]:
    br06_grader = ROOT / "tests/fixtures/coding_tasks/br06_v2_hidden_acceptance.py"
    return [
        ReplaySpec(_original("BR-06", "kimi-k2.7-code", 1), 2, br06_grader),
        ReplaySpec(_original("BR-08", "kimi-k2.7-code", 1), 3, BR08_V2_GRADER),
        ReplaySpec(_original("BR-08", "glm-5.2", 1), 3, BR08_V2_GRADER),
        ReplaySpec(_original("BR-08", "glm-5.2", 2), 3, BR08_V2_GRADER),
        ReplaySpec(_original("BR-08", "deepseek-v4-pro", 1), 3, BR08_V2_GRADER),
        ReplaySpec(_original("BR-08", "deepseek-v4-pro", 2), 3, BR08_V2_GRADER),
    ]


def _replay(spec: ReplaySpec) -> None:
    run_spec = spec.run_spec
    source_raw = experiment.RAW_ROOT / spec.source.run_slug
    source_plan = source_raw / "handoff/plan.json"
    source_prompt = source_raw / "handoff/build-prompt.txt"
    source_execution = json.loads(
        (source_raw / "plan-cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    task = load_coding_task(json.loads(spec.source.task.manifest.read_text(encoding="utf-8")))
    raw = experiment.RAW_ROOT / run_spec.run_slug
    if raw.exists():
        raise ValueError(f"raw replay already exists: {raw}")
    raw.mkdir(parents=True)
    worktree = experiment._worktree(run_spec, task.baseline.commit_sha)

    gate_code = experiment._run(
        [
            str(experiment.PYTHON),
            str(PLAN_GATE_V3),
            str(source_plan),
            spec.source.task.short_id,
        ],
        stdout=raw / "corrected-plan-gate.log",
    )
    if gate_code != 0:
        raise ValueError(f"corrected gate rejected replay source: {spec.source.run_slug}")

    usage = source_execution["usage"]
    build_wall = max(
        1,
        experiment.TOTAL_WALL - (int(source_execution["wall_ms"]) + 999) // 1000,
    )
    build_tools = max(1, experiment.TOTAL_TOOLS - int(usage["tool_calls"]))
    build_tokens = max(1, experiment.TOTAL_TOKENS - int(usage["model_tokens"]))
    source_record = {
        "schema_version": "coding-eval-gate-replay/v0.1",
        "source_attempt_id": spec.source.run_slug,
        "source_plan_sha256": _sha256_file(source_plan),
        "source_build_prompt_sha256": _sha256_file(source_prompt),
        "corrected_gate_version": spec.gate_version,
        "remaining_build_budget": {
            "max_wall_seconds": build_wall,
            "max_tool_calls": build_tools,
            "max_model_tokens": build_tokens,
        },
    }
    (raw / "replay-source.json").write_text(
        canonical_json(source_record) + "\n",
        encoding="utf-8",
    )
    print(f"START {run_spec.run_slug}", flush=True)
    build_code = experiment._run(
        experiment._runner_command(
            run_spec,
            worktree,
            raw,
            cli_name="build-cli.jsonl",
            agent="build",
            prompt_file=source_prompt,
            wall=build_wall,
            tools=build_tools,
            tokens=build_tokens,
        )
    )
    experiment._grade(run_spec, worktree, raw)
    if spec.adjudicated_grader != spec.source.task.grader:
        experiment._run(
            [str(experiment.GRADER_PYTHON), str(spec.adjudicated_grader), "."],
            cwd=worktree,
            stdout=raw / "adjudicated-grader.log",
        )
    experiment._write_end(
        raw,
        {
            "build_started": True,
            "build_runner_exit_code": build_code,
            "reason": "corrected_gate_replay",
            **source_record,
        },
    )
    print(f"END {run_spec.run_slug} build_runner={build_code}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("recovery", "replay", "all"), default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    recoveries = recovery_matrix() if args.phase in {"recovery", "all"} else []
    replays = replay_matrix() if args.phase in {"replay", "all"} else []
    if args.dry_run:
        for spec in recoveries:
            print(f"recovery {spec.run_slug}")
        for spec in replays:
            print(f"replay {spec.run_spec.run_slug} <- {spec.source.run_slug}")
        return 0

    os.environ["TRACELANE_ROOT"] = str(ROOT)
    for spec in recoveries:
        experiment.execute(spec, plan_gate=PLAN_GATE_V3)
    for spec in replays:
        _replay(spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
