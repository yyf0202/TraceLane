from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tracelane.contracts import HarnessConfig, canonical_json
from tracelane.evidence_registry import (
    EvidenceIndexEntry,
    EvidenceQuery,
    EvidenceRoot,
    VerificationReport,
    build_project_index,
    build_registry,
    find_evidence,
    rebuild_project_index,
    rebuild_registry,
    verify_evidence_registry,
)
from tracelane.experiments.runner import (
    ablate_context_policy,
    evaluate_suite,
    inspect_run,
    load_tasks,
    packaged_v01_suite,
)
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime


def _add_evidence_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path("evidence"))
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--status",
        action="append",
        choices=("pending", "approved", "rejected", "superseded"),
        default=[],
    )
    parser.add_argument("--fact")
    parser.add_argument("--domain")
    parser.add_argument("--role", choices=("evidence", "future-control"))
    parser.add_argument("--source-type", choices=("primary", "secondary", "dataset"))
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--json", action="store_true")


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

    evidence = subparsers.add_parser(
        "evidence",
        help="Inspect and verify the research evidence registry.",
    )
    evidence_commands = evidence.add_subparsers(
        dest="evidence_command",
        required=True,
    )
    evidence_list = evidence_commands.add_parser(
        "list",
        help="List deterministic evidence index entries.",
    )
    _add_evidence_query_arguments(evidence_list)
    evidence_find = evidence_commands.add_parser(
        "find",
        help="Find evidence using deterministic filters.",
    )
    _add_evidence_query_arguments(evidence_find)
    evidence_verify = evidence_commands.add_parser(
        "verify",
        help="Verify source records, indexes, and the global registry.",
    )
    evidence_verify.add_argument("--root", type=Path, default=Path("evidence"))
    evidence_verify.add_argument("--project")
    evidence_verify.add_argument("--json", action="store_true")
    evidence_rebuild = evidence_commands.add_parser(
        "rebuild-index",
        help="Create or identity-match deterministic derived indexes.",
    )
    evidence_rebuild.add_argument("--root", type=Path, default=Path("evidence"))
    evidence_rebuild.add_argument("--project", required=True)
    evidence_rebuild.add_argument("--json", action="store_true")
    return parser


def _evidence_query(args: argparse.Namespace) -> EvidenceQuery:
    return EvidenceQuery(
        project_id=args.project,
        statuses=tuple(args.status),
        fact_id=args.fact,
        domain=args.domain,
        role=args.role,
        source_type=args.source_type,
        date_from=args.date_from,
        date_to=args.date_to,
        clean_only=args.clean,
    )


def _print_evidence_entries(
    entries: Sequence[EvidenceIndexEntry],
    *,
    as_json: bool,
) -> None:
    values = [entry.to_dict() for entry in entries]
    if as_json:
        print(canonical_json(values))
        return
    for value in values:
        print(
            f"candidate_id={value['candidate_id']} "
            f"status={value['effective_status']} "
            f"role={value['role']} "
            f"date={value['document_date']} "
            f"source_type={value['source_type']} "
            f"domains={','.join(value['domains'])} "
            f"facts={','.join(value['fact_ids'])}"
        )


def _verification_value(report: VerificationReport) -> dict[str, object]:
    return {
        "project_count": report.project_count,
        "candidate_count": report.candidate_count,
        "review_count": report.review_count,
        "future_control_count": report.future_control_count,
        "status_counts": dict(report.status_counts),
        "registry_sha256": report.registry_sha256,
        "project_index_sha256": report.project_index_sha256,
    }


def _print_verification(report: VerificationReport, *, as_json: bool) -> None:
    value = _verification_value(report)
    if as_json:
        print(canonical_json(value))
        return
    counts = report.status_counts
    print(
        f"projects={report.project_count} "
        f"candidates={report.candidate_count} "
        f"reviews={report.review_count} "
        f"future_controls={report.future_control_count} "
        f"pending={counts['pending']} "
        f"approved={counts['approved']} "
        f"rejected={counts['rejected']} "
        f"superseded={counts['superseded']} "
        f"registry_sha256={report.registry_sha256} "
        f"project_index_sha256={report.project_index_sha256}"
    )


def _rebuild_evidence(root_path: Path, project_id: str) -> dict[str, str]:
    root = EvidenceRoot.open(root_path)
    index_path = root.resolve(f"tracelane://evidence/projects/{project_id}/index.json")
    registry_path = root.resolve("tracelane://evidence/registry.json")
    if not index_path.exists() and registry_path.exists():
        raise ValueError("evidence derived state conflicts")

    build_project_index(root, project_id)
    index_ref = rebuild_project_index(root, project_id)
    build_registry(root)
    registry_ref = rebuild_registry(root)
    report = verify_evidence_registry(root, project_id)
    if (
        report.project_index_sha256 != index_ref.sha256
        or report.registry_sha256 != registry_ref.sha256
    ):
        raise ValueError("evidence derived state changed")
    return {
        "project_id": project_id,
        "project_index_sha256": index_ref.sha256,
        "registry_sha256": registry_ref.sha256,
    }


def _run_evidence(args: argparse.Namespace) -> int:
    try:
        if args.evidence_command in {"list", "find"}:
            entries = find_evidence(args.root, _evidence_query(args))
            _print_evidence_entries(entries, as_json=args.json)
            return 0
        if args.evidence_command == "verify":
            report = verify_evidence_registry(args.root, args.project)
            _print_verification(report, as_json=args.json)
            return 0
        if args.evidence_command == "rebuild-index":
            value = _rebuild_evidence(args.root, args.project)
            if args.json:
                print(canonical_json(value))
            else:
                print(
                    f"project={value['project_id']} "
                    f"project_index_sha256={value['project_index_sha256']} "
                    f"registry_sha256={value['registry_sha256']}"
                )
            return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        print(
            f"tracelane: error: evidence {args.evidence_command} failed",
            file=sys.stderr,
        )
        return 1
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.command == "evidence":
            return _run_evidence(args)
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
