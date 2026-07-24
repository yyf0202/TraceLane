from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import tracelane
from tracelane.v2.contracts import ArtifactRef, content_digest, make_object_id
from tracelane.v2.schema import artifact_ref_definition


def artifact_ref_value(**overrides: object) -> Mapping[str, object]:
    value: dict[str, object] = {
        "kind": "evidence_record",
        "uri": "tracelane://fixtures/v0.2/history/hist-001/case.json",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "size_bytes": 42,
        "schema_id": "tracelane://schemas/case/v2",
    }
    value.update(overrides)
    return value


def test_artifact_ref_round_trips_valid_document() -> None:
    value = artifact_ref_value()

    reference = ArtifactRef.from_dict(value)

    assert reference.uri.endswith("/case.json")
    assert reference.to_dict() == value


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://fixtures/../secret",
        "tracelane://fixtures/v0.2/%2e%2e/secret",
        "file:///tmp/secret",
        "C:/private/file.json",
        "tracelane://Fixtures/v0.2/case.json",
    ],
)
def test_artifact_ref_rejects_unsafe_uri(uri: str) -> None:
    with pytest.raises(ValueError, match="unsafe artifact URI"):
        ArtifactRef.from_dict(artifact_ref_value(uri=uri))


def test_content_digest_omits_only_its_own_digest_field() -> None:
    without_digest = {"schema_id": "example", "object_id": "item_1", "value": 7}
    with_digest = {**without_digest, "content_sha256": "f" * 64}

    assert content_digest(without_digest) == content_digest(with_digest)
    assert content_digest(without_digest) != content_digest({**without_digest, "value": 8})


def test_object_id_is_deterministic_and_prefix_validated() -> None:
    value = {"schema_id": "example", "value": 7}

    assert make_object_id("artifact", value) == make_object_id("artifact", value)
    assert make_object_id("artifact", value).startswith("artifact_")
    with pytest.raises(ValueError, match="prefix"):
        make_object_id("../escape", value)


def test_embedded_artifact_ref_definitions_match_canonical_schema() -> None:
    canonical = artifact_ref_definition()
    schema_root = Path(tracelane.__file__).parent / "schemas" / "v2"
    embedded = []
    for path in sorted(schema_root.glob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        definition = value.get("$defs", {}).get("artifact_ref")
        if definition is not None:
            embedded.append((path.name, definition))
    assert embedded
    assert all(definition == canonical for _, definition in embedded)
