from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tracelane.contracts import HarnessConfig
from tracelane.experiments.runner import (
    ablate_context_policy,
    evaluate_suite,
    inspect_run,
    load_tasks,
    packaged_v01_suite,
)
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracelane",
        description="A trace-first evaluation harness for evidence-grounded agents.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run one packaged offline task.")
    demo.add_argument("--artifacts", type=Path, required=True)
    demo.add_argument("--seed", type=int, default=7)

    evaluate = subparsers.add_parser("eval", help="Evaluate a deterministic task suite.")
    evaluate.add_argument("--suite", type=Path, required=True)
    evaluate.add_argument("--artifacts", type=Path, required=True)
    evaluate.add_argument("--seed", type=int, default=7)

    ablate = subparsers.add_parser("ablate", help="Run a one-variable ablation.")
    ablate.add_argument("--suite", type=Path, required=True)
    ablate.add_argument("--variable", choices=["context_policy"], required=True)
    ablate.add_argument("--artifacts", type=Path, required=True)
    ablate.add_argument("--seed", type=int, default=7)

    inspect = subparsers.add_parser("inspect", help="Inspect one completed run.")
    inspect.add_argument("--run", type=Path, required=True)
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--seed", type=int, default=7, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.command == "demo":
            tasks = load_tasks(packaged_v01_suite())
            task = next(task for task in tasks if task.task_id == "summary-001")
            result = run_task(
                task,
                HarnessConfig(seed=args.seed),
                DeterministicStubRuntime(),
                args.artifacts,
            )
            coverage = 0.0
            if result.answer_path is not None:
                grades_path = result.answer_path.parent / "grades.json"
                grades = json.loads(grades_path.read_text(encoding="utf-8"))
                coverage = float(grades["completion"]["coverage"])
            print(
                f"run_id={result.run_id} status={result.status} completion_coverage={coverage:.3f}"
            )
            return 0 if result.status == "passed" else 1
        if args.command == "eval":
            summary = evaluate_suite(
                load_tasks(args.suite),
                HarnessConfig(seed=args.seed),
                args.artifacts,
            )
            print(
                f"tasks={summary['task_count']} passed={summary['passed_count']} "
                f"pass_rate={summary['pass_rate']:.3f}"
            )
            return 0 if summary["passed_count"] == summary["task_count"] else 1
        if args.command == "ablate":
            experiment_root, summary = ablate_context_policy(
                load_tasks(args.suite),
                args.artifacts,
                seed=args.seed,
            )
            control = summary["arms"]["control"]["pass_rate"]
            treatment = summary["arms"]["treatment"]["pass_rate"]
            print(
                f"experiment={experiment_root.name} "
                f"control_pass_rate={control:.3f} "
                f"treatment_pass_rate={treatment:.3f}"
            )
            return 0
        if args.command == "inspect":
            value = inspect_run(args.run)
            if args.json:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                print(f"run_id={value['run_id']} status={value['status']}")
                print(f"stages={len(value['stages'])}")
                print(f"passed={value['grades']['passed']}")
                total_tokens = value["operations"].get("input_tokens", 0) + value["operations"].get(
                    "output_tokens", 0
                )
                print(
                    f"model_calls={value['operations'].get('model_calls', 0)} "
                    f"total_tokens={total_tokens}"
                )
                print(f"resume_position={value['operations'].get('resume_position')}")
            return 0
    except (OSError, ValueError, StopIteration, KeyError, json.JSONDecodeError) as exc:
        print(f"tracelane: error: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2
