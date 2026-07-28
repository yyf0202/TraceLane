#!/usr/bin/env python3
"""Run one OpenCode phase with enforced wall, tool-call, and provider-token budgets."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from tracelane.contracts import canonical_json


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--raw-directory", required=True, type=Path)
    parser.add_argument("--cli-name", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--agent", choices=("plan", "build"), required=True)
    parser.add_argument("--provider-id", default="opencode-go")
    parser.add_argument("--model-id", default="glm-5.2")
    parser.add_argument("--provider-name")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-service", default="opencode-go-api-key")
    parser.add_argument("--api-key-env", default="OPENCODE_API_KEY")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--max-wall-seconds", type=int, required=True)
    parser.add_argument("--max-tool-calls", type=int, required=True)
    parser.add_argument("--max-model-tokens", type=int, required=True)
    parser.add_argument("--provider-turn-timeout-seconds", type=int, default=300)
    return parser.parse_args()


def _api_key(environment_name: str, service: str) -> str:
    configured = os.environ.get(environment_name, "").strip()
    if configured:
        return configured
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            "opencode-tracelane",
            "-s",
            service,
            "-w",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    key = result.stdout.strip()
    if not key:
        raise ValueError("OpenCode API key is empty")
    return key


def _provider_config(args: argparse.Namespace) -> str | None:
    if args.base_url is None:
        return None
    return canonical_json(
        {
            "provider": {
                args.provider_id: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": args.provider_name or args.provider_id,
                    "env": [args.api_key_env],
                    "options": {
                        "baseURL": args.base_url,
                        "headerTimeout": args.provider_turn_timeout_seconds * 1_000,
                        "chunkTimeout": args.provider_turn_timeout_seconds * 1_000,
                    },
                    "models": {
                        args.model_id: {
                            "name": args.model_id,
                        }
                    },
                }
            }
        }
    )


def _consume(
    path: Path,
    *,
    offset: int,
    pending: bytes,
    metrics: dict[str, int],
    final: bool = False,
) -> tuple[int, bytes]:
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
        offset = handle.tell()
    value = pending + chunk
    lines = value.splitlines(keepends=True)
    pending = b""
    if lines and not lines[-1].endswith((b"\n", b"\r")) and not final:
        pending = lines.pop()
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if row.get("type") == "tool_use":
            metrics["tool_calls"] += 1
        part = row.get("part")
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            continue
        metrics["input_tokens"] += int(tokens.get("input", 0))
        metrics["output_tokens"] += int(tokens.get("output", 0))
        metrics["reasoning_tokens"] += int(tokens.get("reasoning", 0))
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            metrics["cached_input_tokens"] += int(cache.get("read", 0))
    metrics["model_tokens"] = sum(
        metrics[name]
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        )
    )
    return offset, pending


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (PermissionError, ProcessLookupError):
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=10)
        except (PermissionError, ProcessLookupError):
            return
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait()
            except (PermissionError, ProcessLookupError):
                return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except (PermissionError, ProcessLookupError):
            if process.poll() is None:
                process.kill()
            process.wait()


def _budget_observation(
    args: argparse.Namespace,
    metrics: dict[str, int],
    elapsed_ms: int,
) -> dict[str, dict[str, float | int]]:
    values = {
        "wall_ms": (args.max_wall_seconds * 1_000, elapsed_ms),
        "tool_calls": (args.max_tool_calls, metrics["tool_calls"]),
        "model_tokens": (args.max_model_tokens, metrics["model_tokens"]),
    }
    return {
        name: {
            "limit": limit,
            "observed": observed,
            "overshoot": max(0, observed - limit),
            "overshoot_ratio": round(max(0, observed - limit) / limit, 8),
        }
        for name, (limit, observed) in values.items()
    }


def main() -> int:
    args = _arguments()
    if (
        min(
            args.max_wall_seconds,
            args.max_tool_calls,
            args.max_model_tokens,
            args.provider_turn_timeout_seconds,
        )
        <= 0
    ):
        raise ValueError("budgets and provider turn timeout must be positive")
    if not args.binary.is_file() or not args.worktree.is_dir():
        raise ValueError("binary and worktree must exist")
    message = (
        args.prompt_file.read_text(encoding="utf-8")
        if args.prompt_file is not None
        else args.prompt
    )
    assert isinstance(message, str)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    cli_path = args.raw_directory / args.cli_name
    stderr_path = args.raw_directory / f"{args.cli_name}.stderr.log"
    result_path = args.raw_directory / f"{args.cli_name}.execution.json"
    termination_path = args.raw_directory / f"{args.cli_name}.termination.json"
    if (
        cli_path.exists()
        or stderr_path.exists()
        or result_path.exists()
        or termination_path.exists()
    ):
        raise ValueError("phase output paths must not already exist")

    command = [
        str(args.binary),
        "run",
        "--pure",
        "--auto",
        "--model",
        f"{args.provider_id}/{args.model_id}",
        "--agent",
        args.agent,
        "--format",
        "json",
        "--dir",
        str(args.worktree.resolve()),
        "--title",
        args.title,
    ]
    if args.session:
        command.extend(("--session", args.session))
    command.append(message)
    environment = os.environ.copy()
    environment[args.api_key_env] = _api_key(args.api_key_env, args.api_key_service)
    provider_config = _provider_config(args)
    if provider_config is not None:
        environment["OPENCODE_CONFIG_CONTENT"] = provider_config
    environment["OPENCODE_TRACELANE_DIR"] = str(args.raw_directory.resolve())
    environment["OPENCODE_TRACELANE_TURN_TIMEOUT_MS"] = str(
        args.provider_turn_timeout_seconds * 1_000
    )
    environment["OPENCODE_TRACELANE_NO_RETRY"] = "1"
    metrics = {
        "tool_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "model_tokens": 0,
    }
    started = time.monotonic()
    reason = "completed"
    with cli_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=args.worktree,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        offset = 0
        pending = b""
        while process.poll() is None:
            time.sleep(1)
            offset, pending = _consume(
                cli_path,
                offset=offset,
                pending=pending,
                metrics=metrics,
            )
            elapsed = time.monotonic() - started
            if elapsed > args.max_wall_seconds:
                reason = "wall_budget_exhausted"
            elif metrics["tool_calls"] > args.max_tool_calls:
                reason = "tool_budget_exhausted"
            elif metrics["model_tokens"] > args.max_model_tokens:
                reason = "token_budget_exhausted"
            if reason != "completed":
                termination_path.write_text(
                    canonical_json(
                        {
                            "schema_version": "opencode-local-termination/v0.1",
                            "source": "local_budget",
                            "reason": reason,
                            "signal": "SIGTERM",
                            "wall_ms": round(elapsed * 1_000),
                            "usage_at_termination": metrics,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                _terminate(process)
                break
        exit_code = process.poll()
    _consume(cli_path, offset=offset, pending=pending, metrics=metrics, final=True)
    elapsed_ms = round((time.monotonic() - started) * 1_000)
    if reason == "completed" and exit_code != 0:
        reason = "crashed"
    result = {
        "schema_version": "opencode-budget-execution/v0.1",
        "provider": args.provider_id,
        "model": args.model_id,
        "reason": reason,
        "exit_code": exit_code,
        "wall_ms": elapsed_ms,
        "budgets": {
            "max_wall_seconds": args.max_wall_seconds,
            "max_tool_calls": args.max_tool_calls,
            "max_model_tokens": args.max_model_tokens,
            "provider_turn_timeout_seconds": args.provider_turn_timeout_seconds,
            "provider_turn_retries": 0,
        },
        "usage": metrics,
        "budget_observation": _budget_observation(args, metrics, elapsed_ms),
    }
    result_path.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json(result))
    return 0 if reason == "completed" else 124


if __name__ == "__main__":
    raise SystemExit(main())
