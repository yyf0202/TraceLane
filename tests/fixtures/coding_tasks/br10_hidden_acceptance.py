"""Functional-slice acceptance for BR-10 cross-platform sync push safety."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    shell = (repository / "scripts/sync_data.sh").read_text(encoding="utf-8")
    batch = (repository / "scripts/sync_data.bat").read_text(encoding="utf-8")

    def ahead_commits_push() -> None:
        assert len(re.findall(r"\bgit push\b", shell)) >= 2
        assert re.search(
            r"if git diff --cached --quiet; then[\s\S]{0,500}else[\s\S]{0,300}"
            r"git commit[\s\S]{0,200}fi[\s\S]{0,200}git push",
            shell,
        )
        assert re.search(
            r"git diff --cached --quiet[\s\S]{0,500}if !errorlevel! neq 0 "
            r"\([\s\S]{0,300}git commit[\s\S]{0,300}\)[\s\S]{0,200}git push",
            batch,
        )

    def shell_large_file_preflight() -> None:
        assert "git diff --cached --name-only" in shell
        assert "104857600" in shell
        assert "stat -c%s" in shell and "stat -f%z" in shell
        assert re.search(r'if \[ -n "\$[A-Z_]+"\s*\]; then', shell)

    def batch_large_file_preflight() -> None:
        assert "git diff --cached --name-only" in batch
        assert "104857600" in batch
        assert "%%~zf" in batch
        assert re.search(r"if ![A-Z_]+!==1 \([\s\S]{0,300}goto :[A-Z_]+", batch)

    def abort_and_date_semantics() -> None:
        assert "inc-${DATESTR}" in shell and "data-${DATESTR}" in shell
        assert "inc-!DATESTR!" in batch and "data-!DATESTR!" in batch
        assert "^!DATESTR^!" not in batch
        large_branch = shell[shell.index("if [ -n \"$BIG\" ]") :]
        assert large_branch.index("else") < large_branch.index("git push")

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
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
