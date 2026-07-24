from __future__ import annotations

import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|authorization|password)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")


def redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): ("[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER_VALUE.sub("Bearer [REDACTED]", value)
    return value


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def assert_safe_tree(root: str | Path) -> None:
    supplied = Path(root)
    if _is_link_or_reparse(supplied):
        raise ValueError("tree root must not be a link or reparse point")
    resolved_root = supplied.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("tree root must be a directory")
    for descendant in resolved_root.rglob("*"):
        if _is_link_or_reparse(descendant):
            raise ValueError(f"tree contains a link or reparse point: {descendant.name}")
        try:
            descendant.resolve(strict=True).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"tree descendant escapes the root: {descendant.name}") from exc
