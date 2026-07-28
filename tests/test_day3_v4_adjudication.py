from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance_v4.py"
AMENDMENT = ROOT / "fixtures/coding/bericher-v0.9/day3-adjudication-v4.json"
sys.path.insert(0, str(ROOT / "scripts"))

import resume_day3_after_v4_adjudication as recovery  # noqa: E402
import run_day3_coding_eval as day3  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_candidate(repository: Path, *, skip_clean_push: bool = False) -> None:
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    push_condition = '[ "$COMMITTED" -eq 1 ]' if skip_clean_push else "true"
    (scripts / "sync_data.sh").write_text(
        f"""#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INC_DIR="$PROJECT_DIR/data_incremental"
DATA_DIR="$PROJECT_DIR/data"
file_size_bytes() {{
    if [ "$(uname -s)" = Darwin ]; then stat -f%z "$1"; else stat -c%s "$1"; fi
}}
push_one() {{
    cd "$1"
    git add -A
    if [ "$2" = stock ]; then
        while IFS= read -r file; do
            [ "$(file_size_bytes "$file")" -gt 104857600 ] && return 1
        done < <(git diff --cached --name-only --diff-filter=ACM)
    fi
    COMMITTED=0
    if git diff --cached --quiet; then :; else
        git commit -m "$2-${{DATESTR}}"
        COMMITTED=1
    fi
    if {push_condition}; then git push; fi
}}
cmd_push() {{
    DATESTR=$(date +%Y-%m-%d)
    push_one "$INC_DIR" inc
    if [ "${{2:-}}" = --all ]; then push_one "$DATA_DIR" stock || true; fi
}}
case "${{1:-}}" in push) cmd_push "$@" ;; esac
""",
        encoding="utf-8",
    )
    (scripts / "sync_data.bat").write_text(
        """
:CMD_PUSH
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "inc-!DATESTR!"
)
git push
:PUSH_STOCK
for /f %%F in ('git diff --cached --name-only --diff-filter=ACM') do (
  if %%~zF GTR 104857600 goto :PUSH_DONE
)
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "data-!DATESTR!"
)
git push
:PUSH_DONE
""",
        encoding="utf-8",
    )


def _run(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(V4), str(repository)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_v4_accepts_behaviorally_correct_structure(tmp_path: Path) -> None:
    _write_candidate(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_v4_rejects_plausible_code_that_skips_existing_ahead_commit(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path, skip_clean_push=True)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert '"earned": 0' in result.stdout


def test_v4_resume_replaces_only_interrupted_br11_plan() -> None:
    original = day3.matrix()
    remaining = recovery.remaining_matrix()
    interrupted_index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == recovery.INTERRUPTED
    )
    assert remaining[0].run_slug == (
        recovery.INTERRUPTED + "-" + recovery.RESTART_SUFFIX
    )
    assert remaining[1:] == original[interrupted_index + 1 :]


def test_v4_amendment_hashes() -> None:
    value = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(V4) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_v4_adjudication.py"
    ) == frozen["resume_runner_sha256"]
