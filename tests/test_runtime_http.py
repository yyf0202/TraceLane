from __future__ import annotations

from collections.abc import Mapping

import pytest

from tracelane.runtime.http import (
    HttpRuntimeConfig,
    OpenAICompatibleRuntime,
    _extract_json_object,
)


def test_extract_plain_json_object() -> None:
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_strips_code_fences() -> None:
    text = '```json\n{"a": 1}\n```'
    assert _extract_json_object(text) == {"a": 1}


def test_extract_handles_leading_prose_and_nesting() -> None:
    text = 'Here is the result:\n{"outer": {"inner": [1, 2, {"deep": true}]}} done.'
    assert _extract_json_object(text) == {"outer": {"inner": [1, 2, {"deep": True}]}}


def test_extract_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="not an object|did not contain"):
        _extract_json_object("[1, 2, 3]")


def test_extract_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        _extract_json_object("no json here at all")


def test_extract_handles_trailing_prose_after_object() -> None:
    text = '{"a": 1}\n\nI hope this helps!'
    assert _extract_json_object(text) == {"a": 1}


class _FakeRuntime(OpenAICompatibleRuntime):
    def __init__(self, config: HttpRuntimeConfig, body: Mapping[str, object]) -> None:
        super().__init__(config)
        self._body = body
        self.payloads: list[bytes] = []

    def _post(self, payload: bytes) -> Mapping[str, object]:
        self.payloads.append(payload)
        return self._body


def _config() -> HttpRuntimeConfig:
    return HttpRuntimeConfig(
        base_url="https://example.test/v3",
        api_key="test-key",
        model_id="test-model",
        timeout_seconds=5.0,
        max_retries=0,
    )


def _request():
    from datetime import UTC, datetime

    from tracelane.contracts import EvidenceRecord
    from tracelane.runtime.base import ModelRequest

    return ModelRequest(
        run_id="0" * 64,
        stage="analyst",
        role="fundamentals-analyst",
        question="q?",
        evidence=(
            EvidenceRecord(
                evidence_id="ev1",
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                source="s",
                text="t",
                fact_ids=("f1",),
            ),
        ),
        prior_output={},
        seed=7,
    )


def test_complete_parses_content_and_usage() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"direction": "bullish", "confidence": 0.7, "evidence_ids": ["ev1"], "abstained": false, "abstain_reason": null}'
                }
            }
        ],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45},
    }
    runtime = _FakeRuntime(_config(), body)
    response = runtime.complete(_request())
    assert response.content["direction"] == "bullish"
    assert response.input_tokens == 123
    assert response.output_tokens == 45
    assert response.attempt == 1


def test_request_body_uses_model_seed_and_json_mode() -> None:
    body = {"choices": [{"message": {"content": '{"a": 1}'}}], "usage": {}}
    runtime = _FakeRuntime(_config(), body)
    runtime.complete(_request())
    import json

    sent = json.loads(runtime.payloads[0].decode("utf-8"))
    assert sent["model"] == "test-model"
    assert sent["seed"] == 7
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["role"] == "system"
    assert "ev1" in sent["messages"][1]["content"]


def test_no_choices_raises_after_retries() -> None:
    runtime = _FakeRuntime(_config(), {"choices": []})
    with pytest.raises(ValueError, match="model request failed"):
        runtime.complete(_request())


def test_config_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        HttpRuntimeConfig(base_url="https://x", api_key="", model_id="m")


def test_config_loads_from_local_file(tmp_path) -> None:
    import json

    from tracelane.runtime.config import load_http_runtime_config

    cfg_file = tmp_path / "runtime.json"
    cfg_file.write_text(
        json.dumps(
            {
                "base_url": "https://ark.example/v3",
                "api_key": "k",
                "default_model": "glm-5.2",
                "timeout_seconds": 60.0,
                "max_retries": 3,
            }
        ),
        encoding="utf-8",
    )
    config = load_http_runtime_config(cfg_file)
    assert config.model_id == "glm-5.2"
    assert config.timeout_seconds == 60.0
    assert config.max_retries == 3
    override = load_http_runtime_config(cfg_file, model_override="deepseek-v4-pro")
    assert override.model_id == "deepseek-v4-pro"


def test_config_missing_file_raises(tmp_path) -> None:
    from tracelane.runtime.config import load_http_runtime_config

    with pytest.raises(ValueError, match="not found"):
        load_http_runtime_config(tmp_path / "nope.json")
