"""BR-10 v3 functional adjudicator with helper-aware shell analysis.

V1 and v2 remain frozen.  V3 accepts both inline implementations and an
equivalent ``push_repo``/``check_stock_staged_size`` decomposition.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import br10_hidden_acceptance_v2 as v2


def _ordered(content: str, *markers: str) -> None:
    position = -1
    for marker in markers:
        position = content.index(marker, position + 1)


def _helper_main(repository: Path) -> int:
    shell = (repository / "scripts/sync_data.sh").read_text(encoding="utf-8")
    batch = (repository / "scripts/sync_data.bat").read_text(encoding="utf-8")
    push_helper = v2._section(shell, "push_repo()", "\n}")
    size_helper = v2._section(shell, "check_stock_staged_size()", "\n}")
    shell_push = v2._section(shell, "cmd_push()")
    batch_push = v2._section(batch, "\n:CMD_PUSH\n")
    batch_stock = v2._section(
        batch_push,
        'cd /d "%DATA_DIR%"',
        "\n:PUSH_DONE",
    )

    def ahead_commits_push() -> None:
        _ordered(
            push_helper,
            "git add",
            "git diff --cached --quiet",
            "git commit",
            "git push",
        )
        assert re.search(
            r'push_repo\s+"?\$INC_DIR"?[\s\S]{0,120}\b0\b',
            shell_push,
        )
        assert re.search(
            r'push_repo\s+"?\$DATA_DIR"?[\s\S]{0,120}\b1\b',
            shell_push,
        )
        batch_incremental = v2._section(
            batch_push,
            'cd /d "%INC_DIR%"',
            "\n:PUSH_STOCK",
        )
        v2._push_after_batch_commit_decision(batch_incremental)
        v2._push_after_batch_commit_decision(batch_stock)

    def shell_large_file_preflight() -> None:
        assert re.search(r"git diff --cached --name-(only|status)", size_helper)
        assert re.search(r"--diff-filter[=\s]+[ACMR]+", size_helper)
        assert v2._has_threshold(shell)
        size_implementation = size_helper
        if "file_size_bytes" in size_helper:
            size_implementation += v2._section(shell, "file_size_bytes()", "\n}")
        assert "stat -c" in size_implementation and "stat -f" in size_implementation

    def batch_large_file_preflight() -> None:
        assert re.search(
            r"git diff --cached --name-(only|status)", batch_stock, re.IGNORECASE
        )
        assert re.search(r"--diff-filter(?:\^?=|\s+)[ACMR]+", batch_stock)
        assert v2._has_threshold(batch_stock)
        assert "%%~z" in batch_stock or re.search(
            r"(Get-Item|Length)", batch_stock, re.IGNORECASE
        )

    def abort_and_date_semantics() -> None:
        _ordered(
            push_helper,
            "check_stock_staged_size",
            "exit 1",
            "git diff --cached --quiet",
            "git commit",
            "git push",
        )
        assert re.search(
            r'if\s+\[\s+"?\$check_size"?\s+=\s+"?1"?\s+\]',
            push_helper,
        )
        assert (
            "inc-${DATESTR}" in shell and "data-${DATESTR}" in shell
        ) or (
            "${msg_prefix}-${DATESTR}" in shell
            and re.search(r'push_repo\s+"?\$INC_DIR"?\s+"?[^"]+"?\s+"?inc"?', shell_push)
            and re.search(r'push_repo\s+"?\$DATA_DIR"?\s+"?[^"]+"?\s+"?data"?', shell_push)
        )
        assert "inc-!DATESTR!" in batch and "data-!DATESTR!" in batch
        assert "^!DATESTR^!" not in batch
        v2._oversize_guard_precedes_push(batch_stock)

    return v2._score(
        "BR-10",
        [
            ("ahead_commits_push_without_new_commit", 35, ahead_commits_push),
            ("shell_large_file_preflight", 25, shell_large_file_preflight),
            ("batch_large_file_preflight", 25, batch_large_file_preflight),
            ("large_file_abort_and_date_expansion", 15, abort_and_date_semantics),
        ],
    )


def main(repository: Path) -> int:
    shell = (repository / "scripts/sync_data.sh").read_text(encoding="utf-8")
    if re.search(r"(?m)^push_repo\(\)\s*\{", shell):
        return _helper_main(repository)
    return v2.main(repository)


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
