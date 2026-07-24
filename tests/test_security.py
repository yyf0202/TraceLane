from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_contracts import TASK
from tracelane.artifacts import RunStore
from tracelane.contracts import HarnessConfig, load_task
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.security import assert_safe_tree, redact
from tracelane.tracing import TraceRecorder


def test_redact_removes_sensitive_keys_and_bearer_values() -> None:
    value = {
        "api_key": "key-value",
        "nested": {
            "access_token": "token-value",
            "secret": "secret-value",
            "authorization": "Bearer abc.def.ghi",
            "password": "password-value",
            "message": "request failed with Bearer xyz-123_token",
            "safe": "visible",
        },
    }
    redacted = redact(value)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["access_token"] == "[REDACTED]"
    assert redacted["nested"]["secret"] == "[REDACTED]"
    assert redacted["nested"]["authorization"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["message"] == "request failed with Bearer [REDACTED]"
    assert redacted["nested"]["safe"] == "visible"


def test_safe_tree_rejects_symlink_descendant(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows host")
    with pytest.raises(ValueError, match="link|reparse|escape"):
        assert_safe_tree(root)


def test_run_store_rejects_absolute_and_traversal_paths(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path, "a" * 64)
    for unsafe in ("../escape.json", str((tmp_path / "absolute.json").resolve())):
        with pytest.raises(ValueError, match="relative|escapes"):
            store.write_json(unsafe, {"unsafe": True})


def test_observability_failure_is_fail_open_for_valid_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        RunStore,
        "append_jsonl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    warnings_store = RunStore.create(tmp_path / "warning", "a" * 64)
    recorder = TraceRecorder(warnings_store)
    recorder.emit("test.event", {})
    assert recorder.warnings == ("trace write failed: OSError",)

    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path / "run",
    )
    assert result.status == "passed"
    assert result.answer_path is not None
    assert result.answer_path.exists()


def test_validation_failure_never_publishes_answer(tmp_path: Path) -> None:
    class InvalidRuntime(DeterministicStubRuntime):
        def complete(self, request):
            response = super().complete(request)
            if request.stage == "finalize":
                return replace(response, content={"not": "an answer"})
            return response

    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        InvalidRuntime(),
        tmp_path,
    )
    assert result.status == "failed"
    assert result.answer_path is None
    assert not (tmp_path / "runs" / result.run_id / "output" / "answer.json").exists()
