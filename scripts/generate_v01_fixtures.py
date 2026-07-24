from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

CUTOFF = "2026-04-15T12:00:00Z"
LICENSE = "CC0-1.0 synthetic"
TASK_NAME = re.compile(r"^(summary|conflict|pit|recovery)-00[1-3]$")


SCENARIOS = (
    {
        "task_id": "summary-001",
        "question": "Which habitat zone should the survey team prioritize?",
        "expected_facts": {"fact-zone": "The north marsh is the priority habitat zone."},
        "evidence": [
            (
                "2026-04-15T09:00:00Z",
                "The north marsh is the priority habitat zone.",
                ["fact-zone"],
            )
        ],
    },
    {
        "task_id": "summary-002",
        "question": "When does parcel bay C close for inspection?",
        "expected_facts": {"fact-window": "Parcel bay C closes at 16:00 for inspection."},
        "evidence": [
            (
                "2026-04-15T09:10:00Z",
                "Parcel bay C closes at 16:00 for inspection.",
                ["fact-window"],
            )
        ],
    },
    {
        "task_id": "summary-003",
        "question": "Which entrance remains accessible during the drill?",
        "expected_facts": {"fact-entrance": "The east entrance remains accessible."},
        "evidence": [
            (
                "2026-04-15T09:20:00Z",
                "The east entrance remains accessible.",
                ["fact-entrance"],
            )
        ],
    },
    {
        "task_id": "conflict-001",
        "question": "What do the two pressure inspections report?",
        "expected_facts": {"fact-pressure": "The pressure inspections disagree."},
        "evidence": [
            (
                "2026-04-15T09:30:00Z",
                "The first gauge reports stable pressure.",
                ["fact-pressure"],
            ),
            (
                "2026-04-15T09:31:00Z",
                "The second gauge reports unstable pressure.",
                ["fact-pressure"],
            ),
        ],
    },
    {
        "task_id": "conflict-002",
        "question": "What do the two trail inspections report?",
        "expected_facts": {"fact-trail": "The trail inspections disagree."},
        "evidence": [
            (
                "2026-04-15T09:40:00Z",
                "The morning inspection marks the trail open.",
                ["fact-trail"],
            ),
            (
                "2026-04-15T09:41:00Z",
                "The follow-up inspection marks the trail closed.",
                ["fact-trail"],
            ),
        ],
    },
    {
        "task_id": "conflict-003",
        "question": "What do the two tank temperature checks report?",
        "expected_facts": {"fact-temperature": "The temperature checks disagree."},
        "evidence": [
            (
                "2026-04-15T09:50:00Z",
                "The panel check reports a normal tank temperature.",
                ["fact-temperature"],
            ),
            (
                "2026-04-15T09:51:00Z",
                "The handheld check reports an elevated tank temperature.",
                ["fact-temperature"],
            ),
        ],
    },
    {
        "task_id": "pit-001",
        "question": "What was the status of gate four at the cutoff?",
        "expected_facts": {"fact-gate": "Gate four was open at the cutoff."},
        "evidence": [
            (
                "2026-04-15T10:00:00Z",
                "Gate four was open at the cutoff.",
                ["fact-gate"],
            ),
            (
                "2026-04-15T12:00:01Z",
                "Gate four closed after the cutoff.",
                ["fact-gate"],
            ),
        ],
        "future_indexes": [2],
    },
    {
        "task_id": "pit-002",
        "question": "Which pump was active at the cutoff?",
        "expected_facts": {"fact-pump": "Pump blue was active at the cutoff."},
        "evidence": [
            (
                "2026-04-15T10:10:00Z",
                "Pump blue was active at the cutoff.",
                ["fact-pump"],
            ),
            (
                "2026-04-15T12:00:01Z",
                "Pump green became active after the cutoff.",
                ["fact-pump"],
            ),
        ],
        "future_indexes": [2],
    },
    {
        "task_id": "pit-003",
        "question": "Which reading was verified at the cutoff?",
        "expected_facts": {"fact-reading": "Reading seven was verified at the cutoff."},
        "evidence": [
            (
                "2026-04-15T10:20:00Z",
                "Reading seven was verified at the cutoff.",
                ["fact-reading"],
            ),
            (
                "2026-04-15T12:00:01Z",
                "Reading eight was verified after the cutoff.",
                ["fact-reading"],
            ),
        ],
        "future_indexes": [2],
    },
    {
        "task_id": "recovery-001",
        "question": "Which backup unit passed the readiness check?",
        "expected_facts": {"fact-unit": "Backup unit red passed the readiness check."},
        "evidence": [
            (
                "2026-04-15T10:30:00Z",
                "Backup unit red passed the readiness check.",
                ["fact-unit"],
            )
        ],
        "fault_scenario": "fail-once-finalize",
    },
    {
        "task_id": "recovery-002",
        "question": "Which route passed the continuity check?",
        "expected_facts": {"fact-route": "Route delta passed the continuity check."},
        "evidence": [
            (
                "2026-04-15T10:40:00Z",
                "Route delta passed the continuity check.",
                ["fact-route"],
            )
        ],
        "fault_scenario": "fail-once-finalize",
    },
    {
        "task_id": "recovery-003",
        "question": "Which relay passed the restart check?",
        "expected_facts": {"fact-relay": "Relay amber passed the restart check."},
        "evidence": [
            (
                "2026-04-15T10:50:00Z",
                "Relay amber passed the restart check.",
                ["fact-relay"],
            )
        ],
        "fault_scenario": "fail-once-finalize",
    },
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _task_value(scenario: dict[str, object]) -> dict[str, object]:
    task_id = str(scenario["task_id"])
    if not TASK_NAME.fullmatch(task_id):
        raise ValueError(f"unsafe task ID: {task_id}")
    evidence_specs = scenario["evidence"]
    if not isinstance(evidence_specs, list):
        raise ValueError(f"evidence must be a list: {task_id}")
    evidence = [
        {
            "evidence_id": f"{task_id}-ev-{index:02d}",
            "available_at": available_at,
            "source": "tracelane-synthetic-observation",
            "text": text,
            "fact_ids": fact_ids,
        }
        for index, (available_at, text, fact_ids) in enumerate(evidence_specs, start=1)
    ]
    future_indexes = scenario.get("future_indexes", [])
    if not isinstance(future_indexes, list):
        raise ValueError(f"future indexes must be a list: {task_id}")
    expected_facts = scenario["expected_facts"]
    if not isinstance(expected_facts, dict):
        raise ValueError(f"expected facts must be an object: {task_id}")
    return {
        "task_id": task_id,
        "question": scenario["question"],
        "cutoff_at": CUTOFF,
        "expected_facts": expected_facts,
        "completion_facts": sorted(expected_facts),
        "evidence": evidence,
        "future_evidence_ids": [f"{task_id}-ev-{index:02d}" for index in future_indexes],
        "fault_scenario": scenario.get("fault_scenario"),
        "license": LICENSE,
    }


def generate(output: Path) -> None:
    if output.is_symlink():
        raise ValueError("output directory must not be a symlink")
    root = output.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("output directory must not be a symlink")

    task_manifest: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        task = _task_value(scenario)
        task_id = str(task["task_id"])
        target = (root / f"{task_id}.json").resolve(strict=False)
        if target.parent != root:
            raise ValueError("task path escapes output directory")
        payload = _json_bytes(task)
        target.write_bytes(payload)
        task_manifest.append(
            {
                "task_id": task_id,
                "path": target.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "category": task_id.split("-", 1)[0],
            }
        )

    manifest = {
        "suite_id": "tracelane-v0.1",
        "version": "0.1.0",
        "license": LICENSE,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "tasks": task_manifest,
    }
    (root / "manifest.json").write_bytes(_json_bytes(manifest))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the TraceLane v0.1 fixtures.")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generate(args.output)
    print(f"generated {len(SCENARIOS)} tasks in {args.output.resolve(strict=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
