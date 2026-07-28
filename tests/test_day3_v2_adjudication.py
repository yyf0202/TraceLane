from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN_GATE = ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v2.py"
GRADER = ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance_v2.py"


def _plan(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(PLAN_GATE), str(path), "BR-10"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_v2_plan_gate_accepts_slash_status_notation(tmp_path: Path) -> None:
    content = """
    Update sync_data.sh POSIX shell and sync_data.bat Windows batch, preserving independent
    incremental and stock flows. Commit only when staged changes exist, but push ahead
    commits even with no new change. Inspect staged A/C/M entries, reject over 100 MiB,
    use Linux stat -c and macOS stat -f plus Windows batch size. Abort the stock push only,
    let incremental continue, fix delayed expansion to !DATESTR!, and validate bash -n
    plus git diff --check.
    """
    result = _plan(tmp_path, content)
    assert result.returncode == 0, result.stdout


def test_v2_plan_gate_rejects_status_omission_and_wrong_push_semantics(
    tmp_path: Path,
) -> None:
    content = """
    Update sync_data.sh POSIX shell and sync_data.bat Windows batch, preserving independent
    incremental and stock flows. Commit conditionally and skip the push when no new change.
    Check only modified staged files over 100 MiB with Linux stat -c, macOS stat -f and
    Windows batch size. Abort stock only, use !DATESTR!, validate bash -n and git diff.
    """
    result = _plan(tmp_path, content)
    assert result.returncode == 1


def _write_scripts(destination: Path, variable: str, windows_size: str) -> None:
    scripts = destination / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "sync_data.sh").write_text(
        f"""cmd_push() {{
    if [ -d "$INC_DIR/.git" ]; then
        git add -A
        if git diff --cached --quiet; then
            echo "no incremental changes"
        else
            git commit -m "inc-${{DATESTR}}"
        fi
        git push
    fi
    # ---- Stock repo
    if [ -d "$DATA_DIR/.git" ]; then
        git add -A
        MAX_SIZE=$((100 * 1024 * 1024))
        {variable}=""
        while IFS= read -r file; do
            size=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file")
            if [ "$size" -gt "$MAX_SIZE" ]; then
                {variable}=1
            fi
        done < <(git diff --cached --name-only --diff-filter=ACM)
        if [ -n "${{{variable}}}" ]; then
            echo "abort stock push: oversized file"
        else
            if git diff --cached --quiet; then
                echo "no stock changes"
            else
                git commit -m "data-${{DATESTR}}"
            fi
            git push
        fi
    fi
}}
""",
        encoding="utf-8",
    )
    (scripts / "sync_data.bat").write_text(
        f"""
:CMD_PUSH
cd /d "%INC_DIR%"
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "inc-!DATESTR!"
)
git push
:PUSH_STOCK
cd /d "%DATA_DIR%"
git add -A
set {variable}=0
for /f "delims=" %%f in ('git diff --cached --name-only --diff-filter=ACM') do (
    {windows_size}
    if !FSIZE! GTR 104857600 set {variable}=1
)
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "data-!DATESTR!"
)
if !{variable}!==1 (
    echo abort stock push because an oversized file exceeds the limit
    goto :PUSH_DONE
)
git push
:PUSH_DONE
""",
        encoding="utf-8",
    )


def _grade(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GRADER), str(repository)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_v2_grader_accepts_two_equivalent_implementations(
    tmp_path: Path,
) -> None:
    variants = (
        ("OVERSIZED", 'for %%F in ("%%f") do set FSIZE=%%~zF'),
        (
            "BLOCKED",
            'for /f %%s in (\'powershell -Command "(Get-Item -LiteralPath '
            '\\"%%f\\").Length"\') do set FSIZE=%%s',
        ),
    )
    for index, (variable, windows_size) in enumerate(variants):
        repository = tmp_path / str(index)
        _write_scripts(repository, variable, windows_size)
        result = _grade(repository)
        assert result.returncode == 0, result.stdout


def test_v2_grader_rejects_push_trapped_in_commit_branch(tmp_path: Path) -> None:
    repository = tmp_path / "wrong"
    _write_scripts(
        repository,
        "OVERSIZED",
        'for %%F in ("%%f") do set FSIZE=%%~zF',
    )
    shell_path = repository / "scripts/sync_data.sh"
    shell = shell_path.read_text(encoding="utf-8")
    needle = (
        '            git commit -m "inc-${DATESTR}"\n'
        "        fi\n"
        "        git push\n"
    )
    replacement = (
        '            git commit -m "inc-${DATESTR}"\n'
        "            git push\n"
        "        fi\n"
    )
    assert needle in shell
    shell_path.write_text(shell.replace(needle, replacement, 1), encoding="utf-8")
    result = _grade(repository)
    assert result.returncode == 1


def test_v2_grader_rejects_size_check_after_stock_commit(tmp_path: Path) -> None:
    repository = tmp_path / "wrong-order"
    _write_scripts(
        repository,
        "OVERSIZED",
        'for %%F in ("%%f") do set FSIZE=%%~zF',
    )
    shell_path = repository / "scripts/sync_data.sh"
    shell = shell_path.read_text(encoding="utf-8")
    marker = '        git add -A\n        MAX_SIZE='
    assert marker in shell
    shell_path.write_text(
        shell.replace(
            marker,
            '        git add -A\n        git commit -m "data-${DATESTR}"\n'
            "        MAX_SIZE=",
            1,
        ),
        encoding="utf-8",
    )
    result = _grade(repository)
    assert result.returncode == 1
