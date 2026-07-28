from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance_v2.py"
V3 = ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance_v3.py"
AMENDMENT = ROOT / "fixtures/coding/bericher-v0.9/day3-adjudication-v3.json"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import resume_day3_after_v3_adjudication as recovery  # noqa: E402
import run_day3_coding_eval as day3  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_helper_implementation(repository: Path) -> None:
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "sync_data.sh").write_text(
        """MAX_STOCK_SIZE=$((100 * 1024 * 1024))
check_stock_staged_size() {
    while IFS= read -r file; do
        size=$(stat -c %s "$file" 2>/dev/null || stat -f %z "$file")
        [ "$size" -gt "$MAX_STOCK_SIZE" ] && return 1
    done < <(git diff --cached --name-only --diff-filter=ACM)
}
push_repo() {
    local repo_dir="$1" repo_name="$2" msg_prefix="$3" check_size="$4"
    cd "$repo_dir" || exit 1
    git add -A
    if [ "$check_size" = "1" ]; then
        check_stock_staged_size || exit 1
    fi
    if git diff --cached --quiet; then echo clean; else
        git commit -m "${msg_prefix}-${DATESTR}"
    fi
    git push
}
cmd_push() {
    push_repo "$INC_DIR" "Incremental" "inc" 0
    push_repo "$DATA_DIR" "Stock" "data" 1
}
""",
        encoding="utf-8",
    )
    (scripts / "sync_data.bat").write_text(
        """
:CMD_PUSH
cd /d "%INC_DIR%"
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "inc-!DATESTR!"
)
git push
:PUSH_STOCK
cd /d "%DATA_DIR%"
for /f %%F in ('git diff --cached --name-only --diff-filter=ACM') do (
  if %%~zF GTR 104857600 set OVERSIZED=1
)
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "data-!DATESTR!"
)
if !OVERSIZED!==1 goto :PUSH_DONE
git push
:PUSH_DONE
""",
        encoding="utf-8",
    )


def _run(grader: Path, repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(grader), str(repository)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_v3_accepts_helper_abstraction_that_v2_rejects(tmp_path: Path) -> None:
    _write_helper_implementation(tmp_path)
    assert _run(V2, tmp_path).returncode == 1
    result = _run(V3, tmp_path)
    assert result.returncode == 0, result.stdout


def test_v3_preserves_v2_inline_checks(tmp_path: Path) -> None:
    from test_day3_v2_adjudication import _write_scripts

    _write_scripts(
        tmp_path,
        "OVERSIZED",
        'for %%F in ("%%f") do set FSIZE=%%~zF',
    )
    result = _run(V3, tmp_path)
    assert result.returncode == 0, result.stdout


def test_v3_resume_replaces_only_second_interrupted_slot() -> None:
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


def test_v3_amendment_hashes() -> None:
    value = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(V3) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_v3_adjudication.py"
    ) == frozen["resume_runner_sha256"]
