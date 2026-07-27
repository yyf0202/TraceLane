from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracelane.adapters.opencode import (
    diagnose_last_provider_turn,
    import_opencode_session,
    load_opencode_session,
)


def test_import_opencode_session_freezes_and_normalizes_trace(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    rows = [
        {
            "schema": "tracelane-opencode-observation/v0.1",
            "observed_at": "2026-07-27T00:00:00Z",
            "type": "model.request.prepared",
            "session_id": "ses_test",
            "payload": {"event": {"model": {"providerID": "openai", "id": "test"}}},
        },
        {
            "schema": "tracelane-opencode-observation/v0.1",
            "observed_at": "2026-07-27T00:00:01Z",
            "type": "tool.execute.after",
            "session_id": "ses_test",
            "payload": {"output": {"title": "test", "output": "passed"}},
        },
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    session = load_opencode_session(source)
    store = import_opencode_session(
        session,
        tmp_path / "artifacts",
        harness_config={"context_policy": "retrieval"},
        code_revision="test-revision",
    )

    events = [
        json.loads(line)
        for line in (store.run_dir / "trace" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["event_type"] for event in events] == [
        "run.started",
        "context.selected",
        "tool.observed",
        "run.completed",
    ]
    assert (store.run_dir / "input" / "opencode-session.json").exists()


def test_import_preserves_unmapped_opencode_events(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    source.write_text(
        json.dumps(
            {
                "schema": "tracelane-opencode-observation/v0.1",
                "observed_at": "2026-07-27T00:00:00Z",
                "type": "opencode.event",
                "session_id": "ses_test",
                "payload": {"event": {"type": "session.status"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = import_opencode_session(
        load_opencode_session(source),
        tmp_path / "artifacts",
        harness_config={"context_policy": "retrieval"},
        code_revision="test-revision",
    )
    events = [
        json.loads(line)
        for line in (store.run_dir / "trace" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[1]["event_type"] == "opencode.event"


@pytest.mark.parametrize(
    ("event_types", "expected"),
    [
        ([], "request_not_dispatched"),
        (["provider.http.request.started"], "gateway_no_response_headers"),
        (
            ["provider.http.request.started", "provider.http.response.headers"],
            "gateway_no_first_token",
        ),
        (
            [
                "provider.http.request.started",
                "provider.http.response.headers",
                "model.response.first_chunk",
                "model.stream.error",
            ],
            "stream_interrupted",
        ),
        (
            [
                "provider.http.request.started",
                "provider.http.response.headers",
                "model.response.first_chunk",
                "model.response.first_token",
                "model.stream.completed",
            ],
            "model_completed_processor_incomplete",
        ),
        (
            [
                "provider.http.request.started",
                "provider.http.response.headers",
                "model.response.first_chunk",
                "model.response.first_token",
                "model.stream.completed",
                "model.processor.finalized",
            ],
            "completed",
        ),
    ],
)
def test_diagnoses_provider_turn_stage(
    tmp_path: Path, event_types: list[str], expected: str
) -> None:
    source = tmp_path / "session.jsonl"
    types = ["model.turn.created", *event_types]
    rows = [
        {
            "schema": "tracelane-opencode-observation/v0.1",
            "observed_at": f"2026-07-27T00:00:{index:02d}Z",
            "type": event_type,
            "session_id": "ses_test",
            "payload": {
                "request_id": "req_test",
                **({"status": 200} if event_type == "provider.http.response.headers" else {}),
                **({"first_token_ms": 123} if event_type == "model.response.first_token" else {}),
            },
        }
        for index, event_type in enumerate(types)
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    diagnosis = diagnose_last_provider_turn(
        load_opencode_session(source),
        termination={
            "source": "local_budget",
            "reason": "wall_budget_exhausted",
        },
    )

    assert diagnosis.state == expected
    assert diagnosis.local_termination == "wall_budget_exhausted"
    assert diagnosis.http_status == (200 if "provider.http.response.headers" in types else None)
    assert diagnosis.first_token_ms == (123 if "model.response.first_token" in types else None)
