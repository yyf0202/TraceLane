#!/usr/bin/env python3
"""Run the preregistered Day 2 coding-eval matrix strictly serially."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[1]
BERICHER = Path("/Users/efunyang/Desktop/BeRicher_v0.45")
WORK_ROOT = Path("/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work")
BINARY = WORK_ROOT / "opencode-source/packages/opencode/dist/opencode-darwin-arm64/bin/opencode"
RAW_ROOT = ROOT / "artifacts/raw-opencode"
RUNNER = ROOT / "scripts/run_opencode_coding_attempt.py"
HANDOFF = ROOT / "scripts/prepare_opencode_plan_handoff.py"
PLAN_GATE = ROOT / "tests/fixtures/coding_tasks/day2_plan_acceptance.py"
PYTHON = Path(sys.executable)
GRADER_PYTHON = Path("/Users/efunyang/Desktop/BeRicher_v0.45/.venv/bin/python")
BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"

TOTAL_WALL = 1800
TOTAL_TOOLS = 220
TOTAL_TOKENS = 2_000_000
PLAN_WALL = 900
PLAN_TOOLS = 80
PLAN_TOKENS = 1_000_000
WATCHDOG = 300


@dataclass(frozen=True)
class TaskSpec:
    short_id: str
    manifest: Path
    grader: Path


@dataclass(frozen=True)
class AttemptSpec:
    task: TaskSpec
    model: str
    repeat: int
    workflow: str

    @property
    def model_slug(self) -> str:
        return {
            "glm-5.2": "glm52",
            "deepseek-v4-pro": "dsv4pro",
            "kimi-k2.7-code": "k2.7code",
        }[self.model]

    @property
    def run_slug(self) -> str:
        return f"day2-{self.task.short_id.lower()}-{self.model_slug}-r{self.repeat}-{self.workflow}"


TASKS = (
    TaskSpec(
        "BR-06",
        ROOT / "fixtures/coding/bericher-v0.5/tasks/BR-06-adv10-holding-exemption.json",
        ROOT / "tests/fixtures/coding_tasks/br06_hidden_acceptance.py",
    ),
    TaskSpec(
        "BR-07",
        ROOT / "fixtures/coding/bericher-v0.5/tasks/BR-07-factorvae-crash-resilience.json",
        ROOT / "tests/fixtures/coding_tasks/br07_hidden_acceptance.py",
    ),
    TaskSpec(
        "BR-08",
        ROOT / "fixtures/coding/bericher-v0.5/tasks/BR-08-simulation-catchup.json",
        ROOT / "tests/fixtures/coding_tasks/br08_hidden_acceptance.py",
    ),
)
MODELS = ("glm-5.2", "deepseek-v4-pro", "kimi-k2.7-code")


def matrix() -> list[AttemptSpec]:
    rows: list[AttemptSpec] = []
    for task_index, task in enumerate(TASKS):
        rotated = MODELS[task_index:] + MODELS[:task_index]
        for model_index, model in enumerate(rotated):
            for repeat in (1, 2):
                first = "direct-build" if (task_index + model_index + repeat) % 2 else "plan-build"
                second = "plan-build" if first == "direct-build" else "direct-build"
                rows.extend(
                    (
                        AttemptSpec(task, model, repeat, first),
                        AttemptSpec(task, model, repeat, second),
                    )
                )
    return rows


def _run(command: list[str], *, cwd: Path | None = None, stdout: Path | None = None) -> int:
    print("RUN " + " ".join(command[:4]) + (" …" if len(command) > 4 else ""), flush=True)
    if stdout is None:
        return subprocess.run(command, cwd=cwd, check=False).returncode
    with stdout.open("w", encoding="utf-8") as handle:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        ).returncode


def _worktree(spec: AttemptSpec, baseline: str) -> Path:
    destination = WORK_ROOT / f"bericher-{spec.run_slug}"
    if destination.exists():
        raise ValueError(f"worktree already exists: {destination}")
    subprocess.run(
        ["git", "-C", str(BERICHER), "worktree", "add", "--detach", str(destination), baseline],
        check=True,
    )
    return destination


def _prompt(task: object, workflow: str) -> str:
    objective = task.objective
    editable = ", ".join(task.diff_policy.editable_paths)
    validations = "; ".join(task.acceptance.public_commands)
    if workflow == "plan-build":
        return (
            f"Create a precise implementation plan for {task.task_id}. Do not edit files. "
            f"Objective: {objective} Only these paths may be edited during the later build: "
            f"{editable}. Explain the concrete state and timing semantics, implementation "
            f"locations, edge cases, and validation. Public validation: {validations}. "
            "Return one complete build-ready plan as your final answer."
        )
    return (
        f"Implement {task.task_id}. Objective: {objective} Only edit these paths: {editable}. "
        f"Run the relevant checks, including: {validations}. Do not change protected paths. "
        "Complete the implementation and validation autonomously."
    )


def _runner_command(
    spec: AttemptSpec,
    worktree: Path,
    raw: Path,
    *,
    cli_name: str,
    agent: str,
    prompt: str | None = None,
    prompt_file: Path | None = None,
    wall: int,
    tools: int,
    tokens: int,
) -> list[str]:
    command = [
        str(PYTHON),
        str(RUNNER),
        "--binary",
        str(BINARY),
        "--worktree",
        str(worktree),
        "--raw-directory",
        str(raw),
        "--cli-name",
        cli_name,
        "--title",
        spec.run_slug,
        "--agent",
        agent,
        "--provider-id",
        "ark",
        "--provider-name",
        "Ark Coding",
        "--model-id",
        spec.model,
        "--base-url",
        BASE_URL,
        "--api-key-service",
        "ark-coding-api-key",
        "--api-key-env",
        "ARK_API_KEY",
        "--max-wall-seconds",
        str(wall),
        "--max-tool-calls",
        str(tools),
        "--max-model-tokens",
        str(tokens),
        "--provider-turn-timeout-seconds",
        str(WATCHDOG),
    ]
    if prompt_file is not None:
        command.extend(("--prompt-file", str(prompt_file)))
    else:
        assert prompt is not None
        command.extend(("--prompt", prompt))
    return command


def _execution(raw: Path, cli_name: str) -> dict[str, object]:
    return json.loads((raw / f"{cli_name}.execution.json").read_text(encoding="utf-8"))


def _write_end(raw: Path, value: dict[str, object]) -> None:
    (raw / "workflow-end.json").write_text(canonical_json(value) + "\n", encoding="utf-8")


def _grade(spec: AttemptSpec, worktree: Path, raw: Path) -> None:
    _run(
        [str(GRADER_PYTHON), str(spec.task.grader), "."],
        cwd=worktree,
        stdout=raw / "independent-grader.log",
    )
    _run(
        ["git", "diff", "--check"],
        cwd=worktree,
        stdout=raw / "diff-check.log",
    )


def execute(spec: AttemptSpec) -> None:
    task = load_coding_task(json.loads(spec.task.manifest.read_text(encoding="utf-8")))
    raw = RAW_ROOT / spec.run_slug
    if raw.exists():
        raise ValueError(f"raw attempt already exists: {raw}")
    raw.mkdir(parents=True)
    worktree = _worktree(spec, task.baseline.commit_sha)
    print(f"START {spec.run_slug}", flush=True)

    if spec.workflow == "direct-build":
        code = _run(
            _runner_command(
                spec,
                worktree,
                raw,
                cli_name="cli.jsonl",
                agent="build",
                prompt=_prompt(task, spec.workflow),
                wall=TOTAL_WALL,
                tools=TOTAL_TOOLS,
                tokens=TOTAL_TOKENS,
            )
        )
        _grade(spec, worktree, raw)
        _write_end(raw, {"build_started": True, "runner_exit_code": code})
        print(f"END {spec.run_slug} runner={code}", flush=True)
        return

    plan_code = _run(
        _runner_command(
            spec,
            worktree,
            raw,
            cli_name="plan-cli.jsonl",
            agent="plan",
            prompt=_prompt(task, spec.workflow),
            wall=PLAN_WALL,
            tools=PLAN_TOOLS,
            tokens=PLAN_TOKENS,
        )
    )
    if plan_code != 0:
        _grade(spec, worktree, raw)
        _write_end(
            raw,
            {
                "build_started": False,
                "plan_runner_exit_code": plan_code,
                "reason": "plan_phase_failed",
            },
        )
        print(f"END {spec.run_slug} plan_runner={plan_code}", flush=True)
        return

    handoff_dir = raw / "handoff"
    prepare_code = _run(
        [
            str(PYTHON),
            str(HANDOFF),
            "--task",
            str(spec.task.manifest),
            "--plan-cli",
            str(raw / "plan-cli.jsonl"),
            "--output-dir",
            str(handoff_dir),
        ],
        stdout=raw / "handoff.log",
    )
    gate_code = (
        _run(
            [
                str(PYTHON),
                str(PLAN_GATE),
                str(handoff_dir / "plan.json"),
                spec.task.short_id,
            ],
            stdout=raw / "plan-gate.log",
        )
        if prepare_code == 0
        else 1
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if gate_code != 0 or dirty:
        _grade(spec, worktree, raw)
        _write_end(
            raw,
            {
                "build_started": False,
                "plan_runner_exit_code": plan_code,
                "handoff_exit_code": prepare_code,
                "plan_gate_exit_code": gate_code,
                "plan_modified_workspace": bool(dirty),
                "reason": "plan_gate_failed" if gate_code else "plan_modified_workspace",
            },
        )
        print(f"END {spec.run_slug} gate={gate_code} dirty={bool(dirty)}", flush=True)
        return

    plan_execution = _execution(raw, "plan-cli.jsonl")
    usage = plan_execution["usage"]
    build_wall = max(1, TOTAL_WALL - (int(plan_execution["wall_ms"]) + 999) // 1000)
    build_tools = max(1, TOTAL_TOOLS - int(usage["tool_calls"]))
    build_tokens = max(1, TOTAL_TOKENS - int(usage["model_tokens"]))
    build_code = _run(
        _runner_command(
            spec,
            worktree,
            raw,
            cli_name="build-cli.jsonl",
            agent="build",
            prompt_file=handoff_dir / "build-prompt.txt",
            wall=build_wall,
            tools=build_tools,
            tokens=build_tokens,
        )
    )
    _grade(spec, worktree, raw)
    _write_end(
        raw,
        {
            "build_started": True,
            "plan_runner_exit_code": plan_code,
            "plan_gate_exit_code": gate_code,
            "build_runner_exit_code": build_code,
            "remaining_build_budget": {
                "max_wall_seconds": build_wall,
                "max_tool_calls": build_tools,
                "max_model_tokens": build_tokens,
            },
        },
    )
    print(f"END {spec.run_slug} build_runner={build_code}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()
    rows = matrix()
    if args.only:
        rows = [row for row in rows if row.run_slug == args.only]
        if not rows:
            raise ValueError(f"unknown run slug: {args.only}")
    if args.dry_run:
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    for row in rows:
        execute(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
