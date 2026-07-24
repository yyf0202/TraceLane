from __future__ import annotations

import json
from pathlib import Path

from tracelane.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v0.1"


def test_demo_runs_one_task_and_prints_grade_summary(
    tmp_path: Path,
    capsys,
) -> None:
    artifacts = tmp_path / "demo"
    assert main(["demo", "--artifacts", str(artifacts)]) == 0
    output = capsys.readouterr().out
    run_dirs = list((artifacts / "runs").iterdir())
    assert len(run_dirs) == 1
    assert run_dirs[0].name in output
    assert "completion_coverage=1.000" in output
    assert (run_dirs[0] / "output" / "grades.json").exists()


def test_eval_runs_all_twelve_tasks_and_writes_summary(
    tmp_path: Path,
    capsys,
) -> None:
    artifacts = tmp_path / "eval"
    assert (
        main(
            [
                "eval",
                "--suite",
                str(FIXTURES),
                "--artifacts",
                str(artifacts),
            ]
        )
        == 0
    )
    capsys.readouterr()
    summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
    assert summary["task_count"] == 12
    assert summary["passed_count"] == 12
    assert summary["pass_rate"] == 1.0
    assert len(list((artifacts / "runs").iterdir())) == 12


def test_ablate_isolates_arms_and_changes_only_context_policy(
    tmp_path: Path,
    capsys,
) -> None:
    artifacts = tmp_path / "ablate"
    assert (
        main(
            [
                "ablate",
                "--suite",
                str(FIXTURES),
                "--variable",
                "context_policy",
                "--artifacts",
                str(artifacts),
            ]
        )
        == 0
    )
    capsys.readouterr()
    experiment_dirs = list((artifacts / "experiments").iterdir())
    assert len(experiment_dirs) == 1
    experiment = json.loads((experiment_dirs[0] / "experiment.json").read_text(encoding="utf-8"))
    control = experiment["arms"]["control"]
    treatment = experiment["arms"]["treatment"]
    assert {key for key in control if control[key] != treatment[key]} == {"context_policy"}
    assert (experiment_dirs[0] / "control" / "runs").is_dir()
    assert (experiment_dirs[0] / "treatment" / "runs").is_dir()
    summary = json.loads((experiment_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["arms"]["control"]["passed_count"] == 9
    assert summary["arms"]["treatment"]["passed_count"] == 12


def test_inspect_reports_identity_stages_grades_cost_and_resume(
    tmp_path: Path,
    capsys,
) -> None:
    artifacts = tmp_path / "inspect"
    assert main(["demo", "--artifacts", str(artifacts)]) == 0
    capsys.readouterr()
    run_dir = next((artifacts / "runs").iterdir())
    assert main(["inspect", "--run", str(run_dir), "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["run_id"] == run_dir.name
    assert value["identity"]["model_id"] == "deterministic-stub-v1"
    assert value["stages"]
    assert value["validation"]["valid"]
    assert value["grades"]["passed"]
    assert value["operations"]["model_calls"] == 2
    assert "resume_position" in value["operations"]


def test_invalid_arguments_return_two(capsys) -> None:
    assert main(["eval"]) == 2
    assert "usage:" in capsys.readouterr().err.lower()


def test_failed_eval_returns_one(tmp_path: Path, capsys) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    task = {
        "task_id": "incomplete-001",
        "question": "Which second condition is verified?",
        "cutoff_at": "2026-01-10T00:00:00Z",
        "expected_facts": {
            "fact-one": "Condition one is verified.",
            "fact-two": "Condition two is verified.",
        },
        "completion_facts": ["fact-two"],
        "evidence": [
            {
                "evidence_id": "ev-one",
                "available_at": "2026-01-09T00:00:00Z",
                "source": "synthetic-note",
                "text": "Condition one is verified.",
                "fact_ids": ["fact-one"],
            }
        ],
        "future_evidence_ids": [],
        "fault_scenario": None,
        "license": "CC0-1.0 synthetic",
    }
    (suite / "incomplete-001.json").write_text(
        json.dumps(task),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "eval",
                "--suite",
                str(suite),
                "--artifacts",
                str(tmp_path / "failed"),
            ]
        )
        == 1
    )
    assert "pass_rate=0.000" in capsys.readouterr().out
