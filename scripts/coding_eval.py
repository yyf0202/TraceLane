#!/usr/bin/env python3
"""One safe entry point for provider checks, coding cohorts, imports, and reports."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _run(script: str, arguments: list[str]) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *arguments],
        cwd=ROOT,
        check=False,
    ).returncode


def validate_results(paths: list[Path]) -> dict[str, object]:
    attempts: list[dict[str, object]] = []
    stores = 0
    for path in paths:
        source = path.resolve()
        value = json.loads(source.read_text(encoding="utf-8"))
        new_attempts: list[dict[str, object]] = []
        rows = value.get("attempts")
        if isinstance(rows, list):
            new_attempts.extend(row for row in rows if isinstance(row, dict))
        layers = value.get("layers")
        if isinstance(layers, dict):
            for layer in layers.values():
                if isinstance(layer, list):
                    new_attempts.extend(row for row in layer if isinstance(row, dict))
        attempts.extend(new_attempts)
        run_rows = [
            row for row in new_attempts if isinstance(row.get("run_id"), str)
        ]
        for row in run_rows:
            run = source.parent / "runs" / str(row["run_id"])
            required = (
                run / "input/coding-task.json",
                run / "input/attempt.json",
                run / "workspace/final.json",
                run / "output/provider-cost.json",
            )
            if not all(item.is_file() for item in required):
                raise ValueError(f"incomplete run store: {run}")
            stores += 1
    ids = [str(row["attempt_id"]) for row in attempts if "attempt_id" in row]
    if len(ids) != len(set(ids)):
        raise ValueError("result files contain duplicate attempt IDs")
    return {
        "schema_version": "coding-eval-validation/v0.1",
        "result_files": len(paths),
        "attempts": len(attempts),
        "run_stores": stores,
        "valid": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--profile", action="append")
    preflight.add_argument("--output-directory")
    preflight.add_argument("--timeout-seconds", type=int, default=180)

    run_day2 = subparsers.add_parser("run-day2")
    run_day2.add_argument("--only")
    run_day2.add_argument("--dry-run", action="store_true")

    run_recovery = subparsers.add_parser("run-recovery")
    run_recovery.add_argument("--suffix", required=True)
    run_recovery.add_argument(
        "--phase",
        choices=("recovery", "replay", "all"),
        default="recovery",
    )
    run_recovery.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("import-day2")

    import_recovery = subparsers.add_parser("import-recovery")
    import_recovery.add_argument("--suffix", required=True)
    import_recovery.add_argument("--artifact-directory", required=True)
    import_recovery.add_argument("--recovery-only", action="store_true")

    validate = subparsers.add_parser("validate")
    validate.add_argument("--results", action="append", type=Path, required=True)

    subparsers.add_parser("report-day2")
    args = parser.parse_args()

    if args.command == "preflight":
        forwarded = ["--timeout-seconds", str(args.timeout_seconds)]
        for profile in args.profile or []:
            forwarded.extend(("--profile", profile))
        if args.output_directory:
            forwarded.extend(("--output-directory", args.output_directory))
        return _run("preflight_opencode_providers.py", forwarded)
    if args.command == "run-day2":
        forwarded = ["--dry-run"] if args.dry_run else []
        if args.only:
            forwarded.extend(("--only", args.only))
        return _run("run_day2_coding_eval.py", forwarded)
    if args.command == "run-recovery":
        forwarded = [
            "--phase",
            args.phase,
            "--recovery-suffix",
            args.suffix,
        ]
        if args.dry_run:
            forwarded.append("--dry-run")
        return _run("run_day2_recovery.py", forwarded)
    if args.command == "import-day2":
        return _run("import_day2_coding_eval.py", [])
    if args.command == "import-recovery":
        forwarded = [
            "--recovery-suffix",
            args.suffix,
            "--artifact-directory",
            args.artifact_directory,
        ]
        if args.recovery_only:
            forwarded.append("--recovery-only")
        return _run("import_day2_recovery.py", forwarded)
    if args.command == "validate":
        report = validate_results(args.results)
        print(json.dumps(report, sort_keys=True))
        return 0
    if args.command == "report-day2":
        return _run("summarize_day2_layers.py", [])
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
