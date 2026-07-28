"""Behavioral BR-10 adjudicator.

The shell implementation is exercised against isolated local Git remotes.  The
Windows batch path remains structural because cmd.exe is unavailable on macOS.
Frozen v1-v3 outputs are retained separately.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import br10_hidden_acceptance_v2 as v2


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )


def _git(repository: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=repository).stdout.strip()


def _seed_clone(project: Path, name: str) -> tuple[Path, Path, str]:
    remote = project.parent / f"{project.name}-{name}.git"
    _run(["git", "init", "--bare", "-q", str(remote)])
    repository = project / name
    _run(["git", "clone", "-q", str(remote), str(repository)])
    _git(repository, "config", "user.email", "tracelane@example.test")
    _git(repository, "config", "user.name", "TraceLane Test")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repository, "add", "seed.txt")
    _git(repository, "commit", "-qm", "seed")
    branch = _git(repository, "branch", "--show-current")
    _git(repository, "push", "-qu", "origin", branch)
    return repository, remote, branch


def _remote_head(remote: Path, branch: str) -> str:
    return _run(
        ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"]
    ).stdout.strip()


def _ahead_commit(repository: Path, filename: str) -> str:
    (repository / filename).write_text("ahead\n", encoding="utf-8")
    _git(repository, "add", filename)
    _git(repository, "commit", "-qm", "ahead")
    return _git(repository, "rev-parse", "HEAD")


def _copy_script(source: Path, project: Path) -> Path:
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    destination = scripts / "sync_data.sh"
    shutil.copy2(source, destination)
    return destination


def _shell_scenarios(source: Path) -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="tracelane-br10-v4-") as temporary:
        root = Path(temporary)

        ahead_project = root / "ahead-project"
        script = _copy_script(source, ahead_project)
        incremental, incremental_remote, incremental_branch = _seed_clone(
            ahead_project, "data_incremental"
        )
        ahead_sha = _ahead_commit(incremental, "ahead.txt")
        clean_commit_count = _git(incremental, "rev-list", "--count", "HEAD")
        result = _run(["bash", str(script), "push"], check=False)
        ahead_pushed = (
            result.returncode == 0
            and _remote_head(incremental_remote, incremental_branch) == ahead_sha
            and _git(incremental, "rev-list", "--count", "HEAD")
            == clean_commit_count
        )

        guard_project = root / "guard-project"
        guard_script = _copy_script(source, guard_project)
        guard_incremental, guard_inc_remote, guard_inc_branch = _seed_clone(
            guard_project, "data_incremental"
        )
        guard_ahead = _ahead_commit(guard_incremental, "ahead.txt")
        stock, stock_remote, stock_branch = _seed_clone(guard_project, "data")
        stock_before = _git(stock, "rev-parse", "HEAD")
        oversized = stock / "oversized.bin"
        with oversized.open("wb") as handle:
            handle.seek(104_857_600)
            handle.write(b"\0")
        _run(["bash", str(guard_script), "push", "--all"], check=False)
        staged = _git(stock, "diff", "--cached", "--name-only")
        stock_blocked = (
            _git(stock, "rev-parse", "HEAD") == stock_before
            and _remote_head(stock_remote, stock_branch) == stock_before
            and "oversized.bin" in staged
        )
        independent_incremental = (
            _remote_head(guard_inc_remote, guard_inc_branch) == guard_ahead
        )
        return {
            "ahead_pushed_without_new_commit": ahead_pushed,
            "oversized_stock_blocked": stock_blocked,
            "incremental_survived_stock_abort": independent_incremental,
        }


def main(repository: Path) -> int:
    shell_path = repository / "scripts/sync_data.sh"
    shell = shell_path.read_text(encoding="utf-8")
    batch = (repository / "scripts/sync_data.bat").read_text(encoding="utf-8")
    batch_push = v2._section(batch, "\n:CMD_PUSH\n")

    @functools.cache
    def scenarios() -> dict[str, bool]:
        return _shell_scenarios(shell_path)

    def ahead_commits_push() -> None:
        assert scenarios()["ahead_pushed_without_new_commit"]
        direct_pushes = len(re.findall(r"\bgit push\b", batch_push, re.IGNORECASE))
        helper_calls = len(
            re.findall(r"(?im)^\s*call\s+:[A-Z_]*PUSH[A-Z_]*", batch_push)
        )
        assert direct_pushes >= 2 or (
            helper_calls >= 2
            and "git rev-list" in batch_push
            and direct_pushes >= 1
        )

    def shell_large_file_preflight() -> None:
        assert scenarios()["oversized_stock_blocked"]
        assert "stat -c" in shell and "stat -f" in shell

    def batch_large_file_preflight() -> None:
        assert re.search(
            r"git diff --cached --name-(only|status)", batch_push, re.IGNORECASE
        )
        assert re.search(r"--diff-filter(?:\^?=|\s+)[ACMR]+", batch_push)
        assert v2._has_threshold(batch_push)
        assert "%%~z" in batch_push or re.search(
            r"(Get-Item|Length)", batch_push, re.IGNORECASE
        )
        assert re.search(
            r"(GTR|>)\s*(?:104857600|![A-Z_]+!|%[A-Z_]+%)",
            batch_push,
            re.IGNORECASE,
        )
        assert re.search(r"goto\s+:[A-Z_]+|\bexit\s+/b\b", batch_push, re.IGNORECASE)

    def abort_and_date_semantics() -> None:
        assert scenarios()["incremental_survived_stock_abort"]
        assert "inc-!DATESTR!" in batch and "data-!DATESTR!" in batch
        assert "^!DATESTR^!" not in batch

    return v2._score(
        "BR-10",
        [
            ("ahead_commits_push_without_new_commit", 35, ahead_commits_push),
            ("shell_large_file_preflight", 25, shell_large_file_preflight),
            ("batch_large_file_preflight", 25, batch_large_file_preflight),
            ("large_file_abort_and_date_expansion", 15, abort_and_date_semantics),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
