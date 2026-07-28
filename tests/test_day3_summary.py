from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import summarize_day3_coding_eval as summary  # noqa: E402


def test_day3_report_keeps_failures_out_of_paired_capability_delta() -> None:
    rows = []
    for task in ("BR-10",):
        for model in ("glm-5.2",):
            for repeat in (1, 2):
                for workflow in ("direct-build", "plan-build"):
                    eligible = not (repeat == 2 and workflow == "plan-build")
                    rows.append(
                        {
                            "task": task,
                            "model": model,
                            "repeat": repeat,
                            "workflow": workflow,
                            "analysis_functional_score": 60
                            if workflow == "direct-build"
                            else 80,
                            "analysis_functional_possible": 100,
                            "model_tokens": 100,
                            "wall_ms": 1000,
                            "capability_analysis_eligible": eligible,
                            "build_started": workflow == "direct-build" or eligible,
                            "plan_score": 100 if workflow == "plan-build" and eligible else None,
                        }
                    )
    report = summary.build_report({"attempts": rows})
    assert "1/2 pairs are capability-analysis eligible" in report
    assert "| BR-10 | glm-5.2 | 2 | 60 | 80 | excluded" in report
    assert "paired delta averaged +20.0" in report
