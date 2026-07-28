from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import summarize_day2_layers as summary  # noqa: E402


def test_layered_summary_does_not_pool_recovery_or_replay() -> None:
    original = {
        "attempts": [
            {"capability_analysis_eligible": True},
            {"capability_analysis_eligible": False},
        ]
    }
    recovery1 = {
        "layers": {
            "quota_recovery_complete_pairs": [
                {"capability_analysis_eligible": False}
            ],
            "corrected_gate_build_replays": [
                {
                    "task": "BR-X",
                    "model": "m",
                    "frozen_plan_score": 50,
                    "corrected_plan_score": 100,
                    "analysis_functional_score": 30,
                    "execution_reason": "token_budget_exhausted",
                }
            ],
        }
    }
    recovery2 = {
        "layers": {
            "quota_recovery_complete_pairs": [
                {
                    "model": "m",
                    "repeat": 1,
                    "workflow": "direct-build",
                    "analysis_functional_score": 60,
                    "model_tokens": 10,
                },
                {
                    "model": "m",
                    "repeat": 1,
                    "workflow": "plan-build",
                    "analysis_functional_score": 30,
                    "model_tokens": 11,
                },
            ]
        }
    }
    report = summary.build_report(original, recovery1, recovery2)
    assert "| Original preregistered matrix | 2 | 1 slots |" in report
    assert "| Recovery1 | 1 | 0 attempts |" in report
    assert "| Corrected-gate replay | 1 | 1 builds" in report
    assert "mean paired delta -30.0" in report
    assert "not preregistered matrix rows" in report
