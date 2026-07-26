from __future__ import annotations

import json
from pathlib import Path

from tracelane.adapters.opencode import import_opencode_session, load_opencode_session


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
        for line in (store.run_dir / "trace" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events] == [
        "run.started",
        "context.selected",
        "tool.observed",
        "run.completed",
    ]
    assert (store.run_dir / "input" / "opencode-session.json").exists()
