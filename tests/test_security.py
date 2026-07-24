from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_contracts import TASK
from tracelane.artifacts import RunStore
from tracelane.contracts import HarnessConfig, canonical_json, load_task
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.security import assert_safe_tree, classify_and_redact, redact
from tracelane.tracing import TraceRecorder


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("token " + "sk-" + "a" * 24, "sk-"),
        ("https://example.test/?api_key=secret-value", "secret-value"),
        ("mail me at person@example.test", "person@example.test"),
        ("call +86 17610768902", "17610768902"),
        (r"read \\server\share\secret.txt", "server"),
        ("read C:/Users/name/private.txt", "Users/name"),
    ],
)
def test_redact_removes_sensitive_values_inside_ordinary_strings(
    raw: str,
    forbidden: str,
) -> None:
    result = classify_and_redact({"note": raw})
    assert forbidden not in canonical_json(result.value)
    assert result.payload_classification == "restricted"
    assert result.redaction_applied is True


def test_redact_removes_configured_secret_exactly() -> None:
    result = classify_and_redact(
        {"note": "prefix private-runtime-value suffix"},
        secrets=("private-runtime-value",),
    )
    assert result.value == {"note": "prefix [REDACTED] suffix"}


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("token " + "ghp_" + "a" * 36, "ghp_"),
        ("key " + "AKIA" + "A" * 16, "AKIA"),
        ("call +1 (415) 555-2671", "415"),
        ("https://example.test/?access_token=private-value", "private-value"),
        ("https://example.test/?client_secret=private-value", "private-value"),
    ],
)
def test_redact_removes_common_credentials_phones_and_query_aliases(
    raw: str,
    forbidden: str,
) -> None:
    result = classify_and_redact({"note": raw})

    assert forbidden not in canonical_json(result.value)
    assert result.redaction_applied is True


def test_redact_does_not_treat_dates_or_short_numbers_as_phones() -> None:
    result = classify_and_redact({"note": "1812-05-07 and 555-2671"})

    assert result.value == {"note": "1812-05-07 and 555-2671"}
    assert result.redaction_applied is False


def test_redact_removes_sensitive_keys_and_bearer_values() -> None:
    value = {
        "credentials": [
            {"api_key": "key-value"},
            {"access_token": "token-value"},
            {"secret": "secret-value"},
            {"authorization": "Bearer abc.def.ghi"},
            {"password": "password-value"},
        ],
        "nested": {
            "message": "request failed with Bearer xyz-123_token",
            "safe": "visible",
        },
    }
    redacted = redact(value)
    assert redacted["credentials"] == [
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
    ]
    assert redacted["nested"]["message"] == "request failed with Bearer [REDACTED]"
    assert redacted["nested"]["safe"] == "visible"


def test_classify_and_redact_removes_contact_headers_and_local_paths() -> None:
    value = {
        "sensitive_fields": [
            {"cookie": "session=private"},
            {"set-cookie": "session=private"},
            {"email": "person@example.com"},
            {"phone": "17600000000"},
            {"local_path": "D:\\private\\evidence.json"},
        ],
        "message": ("read C:\\Users\\person\\secret.txt and /home/person/private.json"),
        "safe": "visible",
    }

    result = classify_and_redact(value)

    assert result.payload_classification == "restricted"
    assert result.redaction_applied is True
    assert result.value["sensitive_fields"] == [
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
        {"[REDACTED]": "[REDACTED]"},
    ]
    assert result.value["message"] == "read [LOCAL_PATH] and [LOCAL_PATH]"
    assert result.value["safe"] == "visible"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("read /etc/passwd", "read [LOCAL_PATH]"),
        ("inspect /var/tmp/private.txt", "inspect [LOCAL_PATH]"),
        ("open /opt/private/file", "open [LOCAL_PATH]"),
    ],
)
def test_classify_and_redact_removes_non_home_posix_absolute_paths(
    raw: str,
    expected: str,
) -> None:
    result = classify_and_redact({"note": raw})

    assert result.value == {"note": expected}
    assert result.payload_classification == "restricted"
    assert result.redaction_applied is True


def test_classify_and_redact_does_not_treat_https_url_as_local_path() -> None:
    url = "https://example.test/etc/passwd"

    result = classify_and_redact({"source_url": url})

    assert result.value == {"source_url": url}
    assert result.redaction_applied is False


def test_classify_and_redact_marks_unchanged_payload_internal() -> None:
    result = classify_and_redact({"query": "Napoleon 1812"})

    assert result.value == {"query": "Napoleon 1812"}
    assert result.payload_classification == "internal"
    assert result.redaction_applied is False


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
