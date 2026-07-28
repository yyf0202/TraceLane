from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures/coding/harnesses/opencode-h0.json"
SOURCE = Path(
    "/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work/"
    "opencode-source"
)


def test_h0_binds_complete_source_and_binary() -> None:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert value["schema_version"] == "coding-harness-baseline/v0.1"
    commit = value["source"]["commit_sha"]
    tree = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", f"{commit}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tree == value["source"]["tree_sha"]
    binary = SOURCE / value["binary"]["relative_path"]
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == value["binary"]["sha256"]


def test_h0_keeps_observation_and_provider_outside_candidate_scope() -> None:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scope = value["candidate_scope"]
    assert "packages/opencode/src/session/**" in scope["editable_paths"]
    assert "packages/opencode/src/tracelane/**" in scope["protected_paths"]
    assert "packages/opencode/src/plugin/tracelane.ts" in scope["protected_paths"]
    assert "packages/opencode/src/provider/**" in scope["protected_paths"]
    assert value["execution"]["automatic_provider_turn_retries"] == 0
