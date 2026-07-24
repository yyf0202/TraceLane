from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from functools import cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from tracelane.contracts import canonical_json

_SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_UTC_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

UTC_DATE_TIME_FORMAT_CHECKER = FormatChecker()


@UTC_DATE_TIME_FORMAT_CHECKER.checks("date-time")
def is_canonical_utc_date_time(value: object) -> bool:
    if not isinstance(value, str) or not _UTC_DATE_TIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def validate_document_date(document_date: object, date_precision: object) -> None:
    if date_precision not in {"day", "month", "year", "estimated"}:
        raise ValueError("date_precision is invalid")
    if not isinstance(document_date, str):
        raise ValueError("document_date is invalid")
    match = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", document_date)
    if match is None:
        raise ValueError("document_date is invalid")
    expected_parts = {"year": 1, "month": 2, "day": 3}
    supplied_parts = 1 + int(match[2] is not None) + int(match[3] is not None)
    if date_precision != "estimated" and supplied_parts != expected_parts[date_precision]:
        raise ValueError("document_date does not match date_precision")
    year = int(match[1])
    month = int(match[2] or 1)
    day = int(match[3] or 1)
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ValueError("document_date is invalid") from exc


class SchemaValidationError(ValueError):
    """A stable, machine-readable JSON Schema validation error."""

    def __init__(self, *, schema_id: str, pointer: str, message: str) -> None:
        super().__init__(f"{schema_id} at {pointer}: {message}")
        self.code = "schema_validation_failed"
        self.schema_id = schema_id
        self.pointer = pointer


@cache
def _load_schema(name: str) -> Mapping[str, object]:
    if not isinstance(name, str) or not _SCHEMA_NAME.fullmatch(name):
        raise ValueError("schema name is invalid")
    path = files("tracelane").joinpath("schemas", "v2", f"{name}.schema.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"schema is unavailable or invalid: {name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"schema must contain a JSON object: {name}")
    Draft202012Validator.check_schema(value)
    return value


def _json_pointer(path: object) -> str:
    parts = [
        str(part).replace("~", "~0").replace("/", "~1")
        for part in path  # type: ignore[union-attr]
    ]
    return "/" + "/".join(parts)


def artifact_ref_definition() -> dict[str, object]:
    schema = _load_schema("artifact-ref")
    return {
        str(key): json.loads(canonical_json(item))
        for key, item in schema.items()
        if key not in {"$schema", "$id", "title"}
    }


def validate_document(name: str, value: Mapping[str, object]) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("schema document must be a mapping")
    schema = _load_schema(name)
    normalized = json.loads(canonical_json(value))
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=UTC_DATE_TIME_FORMAT_CHECKER,
        ).iter_errors(normalized),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if not errors:
        return
    error = errors[0]
    raise SchemaValidationError(
        schema_id=str(schema["$id"]),
        pointer=_json_pointer(error.absolute_path),
        message=error.message,
    )
