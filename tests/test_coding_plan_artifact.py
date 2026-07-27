from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_coding_contracts import task
from tracelane.coding.plan_artifact import (
    PlanArtifact,
    build_handoff_prompt,
    extract_plan_artifact,
    load_plan_artifact,
)


def test_extract_plan_artifact_binds_task_session_content_and_transcript(tmp_path: Path) -> None:
    source = tmp_path / "plan-cli.jsonl"
    rows = [
        {
            "type": "step_start",
            "sessionID": "ses_plan",
            "part": {"type": "step-start"},
        },
        {
            "type": "text",
            "sessionID": "ses_plan",
            "part": {
                "type": "text",
                "text": "Inspect both data paths, then add a regression test.",
            },
        },
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    plan = extract_plan_artifact(task(), source)

    assert plan.task_sha256 == task().task_sha256
    assert plan.plan_session_id == "ses_plan"
    assert plan.content_sha256 == hashlib.sha256(plan.content.encode()).hexdigest()
    assert load_plan_artifact(plan.to_dict()) == plan


def test_build_handoff_prompt_contains_exact_plan_and_digest() -> None:
    content = "First reproduce the failure, then implement the minimal fix."
    plan = PlanArtifact(
        task_sha256=task().task_sha256,
        plan_session_id="ses_plan",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_cli_sha256="a" * 64,
    )

    prompt = build_handoff_prompt(task(), plan)

    assert content in prompt
    assert plan.content_sha256 in prompt
    assert task().objective in prompt


def test_build_handoff_rejects_plan_for_another_task() -> None:
    content = "Plan."
    plan = PlanArtifact(
        task_sha256="a" * 64,
        plan_session_id="ses_plan",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_cli_sha256="b" * 64,
    )
    with pytest.raises(ValueError, match="different"):
        build_handoff_prompt(task(), plan)
