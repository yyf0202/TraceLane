#!/usr/bin/env python3
"""Materialize a complete buildable OpenCode harness baseline as a git worktree."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "fixtures/coding/harnesses/opencode-h0.json"
DEFAULT_SOURCE = Path(
    "/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work/"
    "opencode-source"
)


def load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "coding-harness-baseline/v0.1":
        raise ValueError("unknown harness baseline schema")
    source = value.get("source")
    if not isinstance(source, dict):
        raise ValueError("harness baseline has no source")
    commit = source.get("commit_sha")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ValueError("harness baseline commit must be a full SHA")
    return value


def materialize(manifest: Path, source: Path, destination: Path) -> None:
    value = load_manifest(manifest)
    if not source.is_dir() or not (source / ".git").exists():
        raise ValueError(f"OpenCode source repository does not exist: {source}")
    if destination.exists():
        raise ValueError(f"harness destination already exists: {destination}")
    source_config = value["source"]
    assert isinstance(source_config, dict)
    commit = str(source_config["commit_sha"])
    resolved = subprocess.run(
        ["git", "-C", str(source), "rev-parse", f"{commit}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if resolved != commit:
        raise ValueError("source repository resolved a different harness commit")
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "worktree",
            "add",
            "--detach",
            str(destination),
            commit,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    materialize(
        args.manifest.resolve(),
        args.source.resolve(),
        args.destination.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
