#!/usr/bin/env python3
"""Import the frozen Day 3 matrix into independent TraceLane run stores."""

from __future__ import annotations

import json
import os
from pathlib import Path

import import_day2_coding_eval as importer
import run_day3_coding_eval as experiment

from tracelane.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts/day3-coding-eval"


def main() -> int:
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    importer.ARTIFACT_ROOT = ARTIFACT_ROOT
    rows = [importer._import(spec) for spec in experiment.matrix()]
    result = {
        "schema_version": "coding-eval-day3/v0.1",
        "experiment": "TraceLane x OpenCode Day 3 cross-task matrix",
        "provider": "ark",
        "models": list(experiment.MODELS),
        "harness": "opencode-h0-06d9803be9",
        "automatic_attempt_retries": 0,
        "claim_scope": (
            "Strictly serial paired descriptive evidence across three new BeRicher "
            "tasks and three models. Results remain provider-stratified and make no "
            "statistical-significance or cross-repository claim."
        ),
        "attempts": rows,
    }
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_ROOT / "results.json").write_text(
        canonical_json(result) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"attempts": len(rows), "output": str(ARTIFACT_ROOT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
