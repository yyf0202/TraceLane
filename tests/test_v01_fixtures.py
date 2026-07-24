from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from tracelane.suite import load_suite

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures" / "v0.1"
GENERATOR = REPOSITORY_ROOT / "scripts" / "generate_v01_fixtures.py"


def test_v01_manifest_freezes_twelve_licensed_tasks() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["suite_id"] == "tracelane-v0.1"
    assert manifest["version"] == "0.1.0"
    assert manifest["license"] == "CC0-1.0 synthetic"
    assert len(manifest["tasks"]) == 12
    assert Counter(item["category"] for item in manifest["tasks"]) == {
        "summary": 3,
        "conflict": 3,
        "pit": 3,
        "recovery": 3,
    }
    assert len(load_suite(FIXTURE_ROOT)) == 12


def test_manifest_hashes_match_final_task_bytes_and_generator() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator_sha256"] == hashlib.sha256(GENERATOR.read_bytes()).hexdigest()
    for item in manifest["tasks"]:
        task_path = FIXTURE_ROOT / item["path"]
        assert task_path.parent == FIXTURE_ROOT
        assert hashlib.sha256(task_path.read_bytes()).hexdigest() == item["sha256"]
        value = json.loads(task_path.read_text(encoding="utf-8"))
        assert value["license"] == "CC0-1.0 synthetic"


def test_fixtures_contain_no_private_or_financial_references() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE_ROOT.glob("*.json"))
    forbidden = re.compile(
        r"baobao|tradingagents|openbb|tencent|kimi|deepseek|"
        r"https?://|[a-z]:[\\/]|api[_ -]?key|authorization|bearer\s+|"
        r"\b(?:nasdaq|nyse|ticker|stock|equity)\b",
        re.IGNORECASE,
    )
    assert forbidden.search(combined) is None


def test_generator_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output in (first, second):
        subprocess.run(
            [sys.executable, str(GENERATOR), "--output", str(output)],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files
