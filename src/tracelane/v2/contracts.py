from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import canonical_json, sha256_json
from tracelane.v2.schema import validate_document

_OBJECT_ID_PREFIX = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_TRACELANE_URI = re.compile(r"^tracelane://[a-z0-9][a-z0-9._/-]*$")


def content_digest(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("content digest input must be a mapping")
    payload = {str(key): item for key, item in value.items() if key != "content_sha256"}
    return sha256_json(payload)


def make_object_id(prefix: str, value: Mapping[str, object]) -> str:
    if not isinstance(prefix, str) or not _OBJECT_ID_PREFIX.fullmatch(prefix):
        raise ValueError("object ID prefix is invalid")
    return f"{prefix}_{content_digest(value)[:24]}"


def _validate_uri(uri: str) -> str:
    if not isinstance(uri, str) or not _TRACELANE_URI.fullmatch(uri):
        raise ValueError("unsafe artifact URI")
    if "%" in uri or "\\" in uri:
        raise ValueError("unsafe artifact URI")
    resource = uri.removeprefix("tracelane://")
    parts = resource.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("unsafe artifact URI")
    return uri


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    uri: str
    media_type: str
    sha256: str
    size_bytes: int
    schema_id: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ArtifactRef:
        uri_value = value.get("uri")
        uri = _validate_uri(uri_value) if isinstance(uri_value, str) else ""
        validate_document("artifact-ref", value)
        reference = cls(
            kind=str(value["kind"]),
            uri=uri,
            media_type=str(value["media_type"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            schema_id=str(value["schema_id"]) if value.get("schema_id") is not None else None,
        )
        return reference

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": self.kind,
            "uri": self.uri,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
        if self.schema_id is not None:
            value["schema_id"] = self.schema_id
        normalized = json.loads(canonical_json(value))
        validate_document("artifact-ref", normalized)
        _validate_uri(self.uri)
        return normalized


def validate_transformation_ref(
    reference: ArtifactRef,
    *,
    label: str = "transformation reference",
) -> ArtifactRef:
    if reference.kind != "evidence_transformation" or reference.schema_id is not None:
        raise ValueError(f"{label} metadata is invalid")
    return reference
