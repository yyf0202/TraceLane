from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tracelane.contracts import sha256_json


def _git(repository: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("workspace must be a readable Git repository") from exc
    return completed.stdout


def _git_bytes(repository: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("workspace Git diff could not be captured") from exc
    return completed.stdout


@dataclass(frozen=True)
class UntrackedFile:
    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class WorkspaceSnapshot:
    baseline_commit: str
    head_commit: str
    patch: str
    patch_sha256: str
    changed_paths: tuple[str, ...]
    untracked_files: tuple[UntrackedFile, ...]
    workspace_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_commit": self.baseline_commit,
            "head_commit": self.head_commit,
            "patch_sha256": self.patch_sha256,
            "changed_paths": list(self.changed_paths),
            "untracked_files": [item.to_dict() for item in self.untracked_files],
            "workspace_sha256": self.workspace_sha256,
        }


def _untracked_files(repository: Path) -> tuple[UntrackedFile, ...]:
    raw_paths = _git(repository, "ls-files", "--others", "--exclude-standard", "-z")
    files: list[UntrackedFile] = []
    for relative in filter(None, raw_paths.split("\0")):
        candidate = repository / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("untracked workspace entry must be a regular file")
        content = candidate.read_bytes()
        files.append(
            UntrackedFile(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        )
    return tuple(sorted(files, key=lambda item: item.path))


def capture_workspace(repository: str | Path, baseline_commit: str) -> WorkspaceSnapshot:
    """Capture the final state relative to one immutable attempt baseline."""
    root = Path(repository).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("workspace must be a Git repository")
    baseline = _git(root, "rev-parse", "--verify", f"{baseline_commit}^{{commit}}").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    patch_bytes = _git_bytes(root, "diff", "--binary", "--full-index", baseline, "--")
    patch = patch_bytes.decode("utf-8", errors="replace")
    tracked_paths = tuple(
        sorted(filter(None, _git(root, "diff", "--name-only", baseline, "--").splitlines()))
    )
    untracked = _untracked_files(root)
    changed_paths = tuple(sorted(set(tracked_paths) | {item.path for item in untracked}))
    patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    workspace_sha256 = sha256_json(
        {
            "baseline_commit": baseline,
            "head_commit": head,
            "patch_sha256": patch_sha256,
            "untracked_files": [item.to_dict() for item in untracked],
        }
    )
    return WorkspaceSnapshot(
        baseline_commit=baseline,
        head_commit=head,
        patch=patch,
        patch_sha256=patch_sha256,
        changed_paths=changed_paths,
        untracked_files=untracked,
        workspace_sha256=workspace_sha256,
    )
