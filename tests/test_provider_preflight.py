from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import preflight_opencode_providers as preflight  # noqa: E402


def test_loads_all_frozen_provider_model_profiles() -> None:
    profiles = preflight.load_profiles(preflight.DEFAULT_PROFILES)
    assert [profile["profile_id"] for profile in profiles] == [
        "ark-glm-5.2",
        "ark-deepseek-v4-pro",
        "ark-kimi-k2.7-code",
        "opencode-go-glm-5.2",
    ]


def test_command_uses_profile_without_exposing_a_key(tmp_path: Path) -> None:
    profile = preflight.load_profiles(preflight.DEFAULT_PROFILES)[0]
    command = preflight._command(
        profile,
        binary=tmp_path / "opencode",
        worktree=tmp_path,
        raw=tmp_path / "raw",
        timeout=90,
    )
    assert "--base-url" in command
    assert "ark-coding-api-key" in command
    assert not any("ark-" in item and len(item) > 40 for item in command)
    assert command[-1].endswith("TRACELANE_PROVIDER_OK")


def test_error_stage_distinguishes_transport_failures(tmp_path: Path) -> None:
    cli = tmp_path / "cli.jsonl"
    cli.write_text(
        json.dumps(
            {
                "type": "error",
                "error": {"data": {"message": "SSE read timed out"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert preflight._error_stage(cli) == "provider_stream_interrupted"
