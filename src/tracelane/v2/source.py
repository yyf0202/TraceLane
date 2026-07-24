from __future__ import annotations

import hashlib
import ipaddress
import re
import urllib.parse
from collections.abc import Sequence

from tracelane.security import classify_and_redact

_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_UNRESERVED_PATH_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_SENSITIVE_SOURCE_QUERY_NAMES = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "token",
        "access_token",
        "access-token",
        "client_secret",
        "client-secret",
        "secret",
        "authorization",
        "password",
        "cookie",
        "cookies",
        "set_cookie",
        "set-cookie",
        "session",
        "session_id",
        "session-id",
        "sessionid",
        "session_token",
        "session-token",
        "sessiontoken",
        "sid",
    }
)


def _canonical_path(path: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(path):
        raise ValueError("source URL path contains an invalid percent escape")

    def normalize(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        if character in _UNRESERVED_PATH_CHARACTERS:
            return character
        return f"%{match.group(1).upper()}"

    canonical = _PERCENT_ESCAPE.sub(normalize, path or "/")
    if any(segment in {".", ".."} for segment in canonical.split("/")):
        raise ValueError("source URL path contains a dot segment")
    return canonical


def _contains_secret(value: str, secrets: Sequence[str]) -> bool:
    return any(secret and secret in value for secret in secrets)


def _reject_sensitive_url_components(
    parsed: urllib.parse.SplitResult,
    *,
    secrets: Sequence[str],
) -> None:
    try:
        decoded_path = urllib.parse.unquote(
            parsed.path,
            encoding="utf-8",
            errors="strict",
        )
        query_pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
    except UnicodeError as exc:
        raise ValueError("source URL encoding is invalid") from exc
    decoded_values = [segment for segment in decoded_path.split("/") if segment]
    decoded_values.extend(component for pair in query_pairs for component in pair)
    if _contains_secret(decoded_path, secrets) or any(
        _contains_secret(component, secrets) for pair in query_pairs for component in pair
    ):
        raise ValueError("source URL contains sensitive data")
    if any(classify_and_redact(value).redaction_applied for value in decoded_values):
        raise ValueError("source URL contains sensitive data")


def canonical_source_url(
    source_url: str,
    *,
    secrets: Sequence[str] = (),
) -> str:
    if not isinstance(source_url, str):
        raise ValueError("source URL must be a string")
    try:
        parsed = urllib.parse.urlsplit(source_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL is invalid") from exc
    if parsed.scheme.casefold() != "https":
        raise ValueError("source URL scheme must be https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source URL credentials are forbidden")
    raw_host = parsed.hostname
    if not raw_host:
        raise ValueError("source URL host is invalid")
    try:
        host = raw_host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("source URL host is invalid") from exc
    if host == "localhost":
        raise ValueError("source URL host is invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("source URL host is not public")
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    if port is not None and port != 443:
        host = f"{host}:{port}"
    try:
        query_pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
        if any(name.casefold() in _SENSITIVE_SOURCE_QUERY_NAMES for name, _value in query_pairs):
            raise ValueError("source URL contains a sensitive query parameter")
    except UnicodeError as exc:
        raise ValueError("source URL query encoding is invalid") from exc
    _reject_sensitive_url_components(parsed, secrets=secrets)
    return urllib.parse.urlunsplit(("https", host, _canonical_path(parsed.path), parsed.query, ""))


def source_locator_sha256(source_url: str) -> str:
    canonical = canonical_source_url(source_url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
