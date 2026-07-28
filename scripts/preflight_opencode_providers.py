#!/usr/bin/env python3
"""Run one non-retried lifecycle probe for every configured provider/model profile."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from tracelane.adapters.opencode import diagnose_last_provider_turn, load_opencode_session
from tracelane.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES = ROOT / "fixtures/coding/provider-profiles.json"
DEFAULT_BINARY = Path(
    "/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work/"
    "opencode-source/packages/opencode/dist/opencode-darwin-arm64/bin/opencode"
)
RUNNER = ROOT / "scripts/run_opencode_coding_attempt.py"


def load_profiles(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "coding-provider-profiles/v0.1":
        raise ValueError("unknown provider profile schema")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("provider profiles must be a non-empty list")
    required = {
        "profile_id",
        "provider_id",
        "provider_name",
        "model_id",
        "base_url",
        "keychain_service",
        "api_key_env",
    }
    seen: set[str] = set()
    normalized: list[dict[str, object]] = []
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != required:
            raise ValueError("provider profile fields are invalid")
        profile_id = profile["profile_id"]
        if not isinstance(profile_id, str) or not profile_id or profile_id in seen:
            raise ValueError("provider profile IDs must be unique non-empty strings")
        if profile["base_url"] is not None and not isinstance(profile["base_url"], str):
            raise ValueError("provider base_url must be a string or null")
        for field in required - {"base_url"}:
            if not isinstance(profile[field], str) or not profile[field]:
                raise ValueError(f"provider profile {field} must be a non-empty string")
        seen.add(profile_id)
        normalized.append(profile)
    return normalized


def _command(
    profile: Mapping[str, object],
    *,
    binary: Path,
    worktree: Path,
    raw: Path,
    timeout: int,
) -> list[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "--binary",
        str(binary),
        "--worktree",
        str(worktree),
        "--raw-directory",
        str(raw),
        "--cli-name",
        "cli.jsonl",
        "--title",
        f"provider-preflight-{profile['profile_id']}",
        "--agent",
        "plan",
        "--provider-id",
        str(profile["provider_id"]),
        "--provider-name",
        str(profile["provider_name"]),
        "--model-id",
        str(profile["model_id"]),
        "--api-key-service",
        str(profile["keychain_service"]),
        "--api-key-env",
        str(profile["api_key_env"]),
        "--max-wall-seconds",
        str(timeout),
        "--max-tool-calls",
        "5",
        "--max-model-tokens",
        "100000",
        "--provider-turn-timeout-seconds",
        str(min(timeout, 60)),
        "--prompt",
        (
            "Provider health preflight only. Do not use tools and do not edit files. "
            "Reply exactly: TRACELANE_PROVIDER_OK"
        ),
    ]
    if profile["base_url"] is not None:
        command[command.index("--api-key-service") : command.index("--api-key-service")] = [
            "--base-url",
            str(profile["base_url"]),
        ]
    return command


def _error_stage(cli_path: Path) -> str | None:
    for line in reversed(cli_path.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        if row.get("type") != "error":
            continue
        error = row.get("error")
        data = error.get("data") if isinstance(error, dict) else None
        message = str(data.get("message", "")) if isinstance(data, dict) else ""
        if "SSE read timed out" in message:
            return "provider_stream_interrupted"
        if "Cannot connect to API" in message:
            return "gateway_no_response_headers"
        return "opencode_provider_error"
    return None


def diagnose(raw: Path) -> dict[str, object]:
    execution = json.loads((raw / "cli.jsonl.execution.json").read_text(encoding="utf-8"))
    sessions = list(raw.glob("ses_*.jsonl"))
    lifecycle: dict[str, object]
    if len(sessions) != 1:
        lifecycle = {"state": "no_observed_provider_turn"}
    else:
        try:
            value = diagnose_last_provider_turn(load_opencode_session(sessions[0]))
        except ValueError as exc:
            lifecycle = {"state": "no_observed_provider_turn", "error": str(exc)}
        else:
            lifecycle = {
                "request_id": value.request_id,
                "state": value.state,
                "http_status": value.http_status,
                "first_token_ms": value.first_token_ms,
                "last_response_at": value.last_response_at,
            }
    failure_stage = _error_stage(raw / "cli.jsonl")
    healthy = (
        execution["reason"] == "completed"
        and lifecycle["state"] == "completed"
        and failure_stage is None
    )
    return {
        "healthy": healthy,
        "execution_reason": execution["reason"],
        "failure_stage": failure_stage,
        "usage": execution["usage"],
        "wall_ms": execution["wall_ms"],
        "lifecycle": lifecycle,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--profile", action="append")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--worktree", type=Path, default=ROOT)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    profiles = load_profiles(args.profiles.resolve())
    if args.profile:
        selected = set(args.profile)
        profiles = [profile for profile in profiles if profile["profile_id"] in selected]
        missing = selected - {str(profile["profile_id"]) for profile in profiles}
        if missing:
            raise ValueError(f"unknown provider profiles: {sorted(missing)}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output_directory.resolve()
        if args.output_directory
        else ROOT / "artifacts/provider-preflight" / stamp
    )
    if output.exists():
        raise ValueError(f"preflight output already exists: {output}")
    output.mkdir(parents=True)
    results = []
    for profile in profiles:
        raw = output / str(profile["profile_id"])
        code = subprocess.run(
            _command(
                profile,
                binary=args.binary.resolve(),
                worktree=args.worktree.resolve(),
                raw=raw,
                timeout=args.timeout_seconds,
            ),
            cwd=ROOT,
            check=False,
        ).returncode
        results.append(
            {
                "profile_id": profile["profile_id"],
                "provider_id": profile["provider_id"],
                "model_id": profile["model_id"],
                "runner_exit_code": code,
                **diagnose(raw),
            }
        )
    report = {
        "schema_version": "coding-provider-preflight/v0.1",
        "automatic_retries": 0,
        "formal_experiment_attempt": False,
        "results": results,
        "healthy": all(result["healthy"] for result in results),
    }
    (output / "preflight.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
    print(canonical_json(report))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
