from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_day2_recovery as recovery_import  # noqa: E402
import run_day2_coding_eval as day2  # noqa: E402
import run_day2_recovery as recovery  # noqa: E402


def test_recovery_matrix_is_frozen_and_unique() -> None:
    rows = recovery.recovery_matrix()
    assert [row.run_slug for row in rows] == [
        "day2-br-07-v2-k2.7code-r2-plan-build-recovery1",
        "day2-br-07-v2-k2.7code-r2-direct-build-recovery1",
        "day2-br-07-v2-glm52-r1-plan-build-recovery1",
        "day2-br-07-v2-glm52-r1-direct-build-recovery1",
        "day2-br-07-v2-glm52-r2-direct-build-recovery1",
        "day2-br-07-v2-glm52-r2-plan-build-recovery1",
    ]
    assert len({row.run_slug for row in rows}) == 6


def test_gate_replays_point_to_six_original_plan_attempts() -> None:
    rows = recovery.replay_matrix()
    assert len({row.run_spec.run_slug for row in rows}) == 6
    assert [row.source.run_slug for row in rows] == [
        "day2-br-06-v2-k2.7code-r1-plan-build",
        "day2-br-08-k2.7code-r1-plan-build",
        "day2-br-08-glm52-r1-plan-build",
        "day2-br-08-glm52-r2-plan-build",
        "day2-br-08-dsv4pro-r1-plan-build",
        "day2-br-08-dsv4pro-r2-plan-build",
    ]
    assert all(row.source.workflow == "plan-build" for row in rows)


def test_run_suffix_does_not_change_original_matrix_ids() -> None:
    assert len(day2.matrix()) == 36
    assert all(not row.run_slug.endswith("-recovery1") for row in day2.matrix())


def test_cli_failure_stage_distinguishes_stream_and_connection(tmp_path: Path) -> None:
    stream = tmp_path / "stream.jsonl"
    stream.write_text(
        json.dumps(
            {
                "type": "error",
                "error": {"data": {"message": "SSE read timed out"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    connection = tmp_path / "connection.jsonl"
    connection.write_text(
        json.dumps(
            {
                "type": "error",
                "error": {
                    "data": {
                        "message": "Cannot connect to API: Unable to connect.",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert recovery_import._cli_failure_stage(stream) == "provider_stream_interrupted"
    assert recovery_import._cli_failure_stage(connection) == "gateway_no_response_headers"
