from __future__ import annotations

import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tracelane.contracts import canonical_json

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|authorization|password|cookie|"
    r"set[_-]?cookie|email|phone|local[_-]?path)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_API_KEY_VALUE = re.compile(r"\b(?:sk|ark)-[A-Za-z0-9_-]{16,}\b")
_GITHUB_TOKEN_VALUE = re.compile(
    r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|"
    r"github_pat_[A-Za-z0-9_]{22,255})(?![A-Za-z0-9_])"
)
_AWS_ACCESS_KEY_ID_VALUE = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|token|access[_-]?token|"
    r"client[_-]?secret|secret|authorization|password|cookies?|"
    r"set[_-]?cookie|session(?:[_-]?(?:id|token))?)=)[^&#\s]+"
)
_EMAIL_VALUE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_VALUE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_US_PHONE_VALUE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})"
    r"[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)
_UNC_PATH = re.compile(r"(?i)(?<![A-Z0-9_])\\\\[^\\\s]+\\[^\s]+")
_WINDOWS_LOCAL_PATH = re.compile(r"(?i)(?<![a-z0-9])(?:[a-z]:\\(?:[^\\\s]+\\)*[^\\\s]+)")
_FORWARD_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Z0-9_])[A-Z]:/[^\s]+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![a-zA-Z0-9._:/-])/(?!/)(?:[^\s/?#]+/)*[^\s/?#]+")


@dataclass(frozen=True)
class RedactedPayload:
    value: object
    payload_classification: str
    redaction_applied: bool


def _redact_string(value: str, secrets: Sequence[str]) -> str:
    sanitized = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _BEARER_VALUE.sub("Bearer [REDACTED]", sanitized)
    sanitized = _API_KEY_VALUE.sub("[REDACTED]", sanitized)
    sanitized = _GITHUB_TOKEN_VALUE.sub("[REDACTED]", sanitized)
    sanitized = _AWS_ACCESS_KEY_ID_VALUE.sub("[REDACTED]", sanitized)
    sanitized = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _EMAIL_VALUE.sub("[EMAIL]", sanitized)
    sanitized = _PHONE_VALUE.sub("[PHONE]", sanitized)
    sanitized = _US_PHONE_VALUE.sub("[PHONE]", sanitized)
    sanitized = _UNC_PATH.sub("[LOCAL_PATH]", sanitized)
    sanitized = _WINDOWS_LOCAL_PATH.sub("[LOCAL_PATH]", sanitized)
    sanitized = _FORWARD_WINDOWS_PATH.sub("[LOCAL_PATH]", sanitized)
    return _POSIX_ABSOLUTE_PATH.sub("[LOCAL_PATH]", sanitized)


def redact(value: object, *, secrets: Sequence[str] = ()) -> object:
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            original_key = str(key)
            sensitive_key = _SENSITIVE_KEY.search(original_key) is not None
            sanitized_key = _redact_string(original_key, secrets)
            if sensitive_key or sanitized_key != original_key:
                sanitized_key = "[REDACTED]"
            if sanitized_key in redacted:
                raise ValueError("redacted mapping key collision")
            redacted[sanitized_key] = (
                "[REDACTED]" if sensitive_key else redact(item, secrets=secrets)
            )
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return _redact_string(value, secrets)
    return value


def classify_and_redact(
    value: object,
    *,
    secrets: Sequence[str] = (),
) -> RedactedPayload:
    canonical_json(value)
    sanitized = redact(value, secrets=secrets)
    changed = canonical_json(sanitized) != canonical_json(value)
    return RedactedPayload(
        value=sanitized,
        payload_classification="restricted" if changed else "internal",
        redaction_applied=changed,
    )


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
