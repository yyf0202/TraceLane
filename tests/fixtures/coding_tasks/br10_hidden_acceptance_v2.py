"""Versioned functional-slice adjudicator for BR-10.

The frozen v1 grader remains unchanged.  V2 checks control-flow properties and
accepts equivalent variable names and Windows file-size implementations.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path


def _section(content: str, start: str, end: str | None = None) -> str:
    begin = content.index(start)
    if end is None:
        return content[begin:]
    finish = content.find(end, begin + len(start))
    return content[begin:] if finish < 0 else content[begin:finish]


def _push_after_shell_commit_decision(section: str) -> None:
    decision = section.index("git diff --cached --quiet")
    push = section.index("git push", decision)
    lines = section[decision:push].splitlines()
    assert any(line.strip() == "fi" for line in lines)


def _push_after_batch_commit_decision(section: str) -> None:
    decision = section.index("git diff --cached --quiet")
    push = section.index("git push", decision)
    between = section[decision:push]
    assert re.search(r"(?m)^\s*\)\s*$", between)
    for match in re.finditer(
        r"goto\s+:(PUSH_STOCK|PUSH_DONE)", between, re.IGNORECASE
    ):
        context = between[max(0, match.start() - 500) : match.start()]
        assert re.search(r"oversiz|too.large|exceed", context, re.IGNORECASE)


def _has_threshold(content: str) -> bool:
    return "104857600" in content or bool(
        re.search(r"100\s*\*\s*1024\s*\*\s*1024", content)
    )


def _oversize_guard_precedes_push(section: str) -> None:
    match = re.search(r"104857600|100\s*\*\s*1024\s*\*\s*1024", section)
    assert match is not None
    threshold = match.start()
    commit = section.index("git commit")
    push = section.index("git push")
    assert threshold < commit < push
    guarded = section[threshold:push]
    assert re.search(r"abort|oversiz|too.large|exceed", guarded, re.IGNORECASE)
    assert re.search(
        r"\b(return|exit)\b|goto\s+:[A-Z_]+|\belse\b",
        guarded,
        re.IGNORECASE,
    )


def main(repository: Path) -> int:
    shell = (repository / "scripts/sync_data.sh").read_text(encoding="utf-8")
    batch = (repository / "scripts/sync_data.bat").read_text(encoding="utf-8")
    shell_push = _section(shell, "cmd_push()")
    shell_incremental = _section(
        shell_push,
        'if [ -d "$INC_DIR/.git" ]',
        "# ---- Stock repo",
    )
    shell_stock = _section(shell_push, 'if [ -d "$DATA_DIR/.git" ]')
    batch_push = _section(batch, "\n:CMD_PUSH\n")
    batch_incremental = _section(
        batch_push,
        'cd /d "%INC_DIR%"',
        "\n:PUSH_STOCK",
    )
    batch_stock = _section(
        batch_push,
        'cd /d "%DATA_DIR%"',
        "\n:PUSH_DONE",
    )

    def ahead_commits_push() -> None:
        assert shell_push.count("git push") >= 2
        assert len(re.findall(r"\bgit push\b", batch_push, re.IGNORECASE)) >= 2
        _push_after_shell_commit_decision(shell_incremental)
        _push_after_shell_commit_decision(shell_stock)
        _push_after_batch_commit_decision(batch_incremental)
        _push_after_batch_commit_decision(batch_stock)

    def shell_large_file_preflight() -> None:
        assert re.search(r"git diff --cached --name-(only|status)", shell_stock)
        assert re.search(r"--diff-filter[=\s]+[ACMR]+", shell_stock)
        assert _has_threshold(shell_stock)
        assert "stat -c%s" in shell and "stat -f%z" in shell

    def batch_large_file_preflight() -> None:
        assert re.search(
            r"git diff --cached --name-(only|status)", batch_stock, re.IGNORECASE
        )
        assert re.search(r"--diff-filter(?:\^?=|\s+)[ACMR]+", batch_stock)
        assert _has_threshold(batch_stock)
        assert "%%~z" in batch_stock or re.search(
            r"(Get-Item|Length)", batch_stock, re.IGNORECASE
        )

    def abort_and_date_semantics() -> None:
        _oversize_guard_precedes_push(shell_stock)
        _oversize_guard_precedes_push(batch_stock)
        assert "inc-${DATESTR}" in shell and "data-${DATESTR}" in shell
        assert "inc-!DATESTR!" in batch and "data-!DATESTR!" in batch
        assert "^!DATESTR^!" not in batch

    return _score(
        "BR-10",
        [
            ("ahead_commits_push_without_new_commit", 35, ahead_commits_push),
            ("shell_large_file_preflight", 25, shell_large_file_preflight),
            ("batch_large_file_preflight", 25, batch_large_file_preflight),
            ("large_file_abort_and_date_expansion", 15, abort_and_date_semantics),
        ],
    )


def _score(task: str, checks: list[tuple[str, int, Callable[[], None]]]) -> int:
    outcomes, earned = [], 0
    for name, points, check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            earned += points
            outcomes.append({"name": name, "points": points, "earned": points})
    print(
        "TRACELANE_SCORE="
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
