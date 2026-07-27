from __future__ import annotations

import pytest

from tracelane.coding.contracts import (
    AcceptanceSpec,
    AttemptEnd,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
    SessionRef,
)


def task() -> CodingTask:
    return CodingTask(
        task_id="BR-01-pit-value-date",
        version=1,
        baseline=RepositoryBaseline(
            repository_id="bericher-v0.45",
            commit_sha="a" * 40,
            tree_sha256="b" * 64,
        ),
        objective="Keep PIT date calculation safe when f_ann_date is missing.",
        acceptance=AcceptanceSpec(
            public_commands=("python -m pytest tests/test_pit_filter.py -q",),
            hidden_commands=("python -m pytest /grader/hidden/test_missing_date.py -q",),
        ),
        diff_policy=DiffPolicy(
            editable_paths=("src/components/data_fetcher_tushare.py", "tests/test_pit_filter.py"),
            protected_paths=("data/**", "saved_models/**", "paper_trading_data/**", ".env"),
        ),
        interaction=InteractionScript(mode="scripted_optional", requires_approval=True),
        allowed_commands=("python -m pytest", "git", "rg"),
        max_wall_seconds=900,
        max_tool_calls=100,
        max_model_tokens=100_000,
    )


def test_coding_task_is_immutable_and_has_stable_digest() -> None:
    first = task()
    assert first.task_sha256 == task().task_sha256
    assert first.to_dict()["schema_version"] == "coding-task/v0.1"
    with pytest.raises((AttributeError, TypeError)):
        first.diff_policy.editable_paths += ("src/extra.py",)


def test_coding_task_rejects_overlapping_writable_and_protected_paths() -> None:
    with pytest.raises(ValueError, match="overlap"):
        DiffPolicy(editable_paths=("src/**",), protected_paths=("src/private/**",))


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/outside.py"])
def test_coding_task_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        DiffPolicy(editable_paths=(path,), protected_paths=())


def test_live_interaction_is_valid_but_not_replayable() -> None:
    interaction = InteractionScript(mode="live", requires_approval=False)
    assert interaction.replayable is False
    assert InteractionScript(mode="scripted_optional", requires_approval=True).replayable is True


def test_attempt_end_and_session_refs_capture_optional_phase_tree() -> None:
    child = SessionRef(
        session_id="ses_explore",
        root_session_id="ses_build",
        parent_session_id="ses_build",
        agent_kind="explore",
    )
    finished = AttemptEnd(reason="completed", final_answer="Tests pass.")
    assert child.relationship == "child"
    assert finished.to_dict()["reason"] == "completed"


def test_attempt_end_rejects_unknown_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        AttemptEnd(reason="maybe", final_answer=None)
