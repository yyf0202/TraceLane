from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tracelane.acquisition import EvidenceCandidate, ManualAcquisitionService
from tracelane.contracts import canonical_json
from tracelane.evidence_registry import EvidenceImportMetadata, EvidenceImportRow
from tracelane.hist001 import (
    HIST001_CURATOR,
    HIST001_RETRIEVED_AT,
    HIST001_SESSION_ID,
    HIST001_SOURCE_MANIFEST,
    CandidateSpec,
)
from tracelane.security import classify_and_redact
from tracelane.v2.contracts import content_digest
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import atomic_write_bytes, secure_read_bytes

SPECS = HIST001_SOURCE_MANIFEST


@dataclass(frozen=True)
class PreparationResult:
    review_path: Path
    metadata_path: Path


def _authenticated_manifest(
    artifact_root: Path,
    service: ManualAcquisitionService,
) -> tuple[dict[str, object], bytes]:
    manifest_path = service.session_dir / "manifest.json"
    data = secure_read_bytes(
        manifest_path,
        root=artifact_root,
        label="preparation manifest",
    )
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        validate_document("acquisition-session", value)
        if content_digest(value) != value["content_sha256"]:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("preparation manifest is invalid") from exc
    return value, data


def prepare(artifact_root: Path) -> PreparationResult:
    artifact_root = Path(artifact_root)
    service = ManualAcquisitionService(
        artifact_root,
        session_id=HIST001_SESSION_ID,
        clock=lambda: HIST001_RETRIEVED_AT,
    )
    rows: list[tuple[CandidateSpec, EvidenceCandidate, str, str]] = []
    for spec in SPECS:
        review_fields = classify_and_redact(
            {
                "license_basis": spec.license_basis,
                "note": spec.note,
            }
        )
        if not isinstance(review_fields.value, Mapping):
            raise ValueError("redacted preparation fields must remain an object")
        license_basis = str(review_fields.value["license_basis"])
        note = str(review_fields.value["note"])
        for document_date in spec.document_date.split("/"):
            date_precision = {
                4: "year",
                7: "month",
                10: "day",
            }[len(document_date)]
            candidate = service.ingest(
                query=spec.query,
                title=spec.title,
                source_url=spec.source_url,
                document_date=document_date,
                date_precision=date_precision,
                curated_text=note + "\n",
                curator=HIST001_CURATOR,
            )
            rows.append((spec, candidate, license_basis, note))

    manifest, manifest_bytes = _authenticated_manifest(artifact_root, service)
    closures = service.snapshot_candidates()
    _, confirmed_manifest_bytes = _authenticated_manifest(artifact_root, service)
    if confirmed_manifest_bytes != manifest_bytes:
        raise ValueError("preparation source changed")
    candidates = {closure.candidate.candidate_id: closure.candidate for closure in closures}
    if len(candidates) != len(rows) or set(candidates) != {
        candidate.candidate_id for _, candidate, _, _ in rows
    }:
        raise ValueError("preparation source changed")

    review_path = service.session_dir / "candidate-review.md"
    lines = [
        "# HIST-001 Evidence Candidate Review",
        "",
        "Status: PENDING USER REVIEW",
        "",
        "Approve only if the locator, date, permitted paraphrase, license basis, "
        "and supported fact IDs are acceptable. Formal review objects are not "
        "created until a real reviewer explicitly approves.",
        "",
    ]
    for index, (spec, candidate, license_basis, note) in enumerate(
        rows,
        start=1,
    ):
        lines.extend(
            [
                f"## {index}. {candidate.title}",
                "",
                f"- Candidate ID: `{candidate.candidate_id}`",
                f"- Source: {candidate.source_url}",
                f"- Query: `{candidate.query}`",
                f"- Document date: `{candidate.document_date}`",
                f"- Date precision: `{candidate.date_precision}`",
                f"- Source type: `{spec.source_type}`",
                f"- Fact IDs: `{', '.join(spec.fact_ids)}`",
                f"- Content SHA-256: `{candidate.content_sha256}`",
                f"- License basis: {license_basis}",
                f"- Permitted paraphrase: {note}",
                "- Decision: `PENDING`",
                "- Reviewer: `PENDING`",
                "",
            ]
        )
    atomic_write_bytes(
        review_path,
        "\n".join(lines).encode("utf-8"),
        root=artifact_root,
        label="candidate review",
    )

    metadata = EvidenceImportMetadata.create(
        project_id="hist-001",
        session_id=str(manifest["session_id"]),
        manifest_sha256=str(manifest["content_sha256"]),
        candidates=tuple(
            sorted(
                (
                    EvidenceImportRow.from_dict(
                        {
                            "source_spec_id": spec.source_spec_id,
                            "candidate_id": candidate.candidate_id,
                            "candidate_record_sha256": (
                                candidates[candidate.candidate_id].record_sha256
                            ),
                            "candidate_content_sha256": candidate.content_sha256,
                            "source_type": spec.source_type,
                            "license_basis": license_basis,
                            "content_authorship": "repository_authored",
                            "retention_policy": "paraphrase_only",
                            "domains": sorted(spec.domains),
                            "fact_ids": sorted(spec.fact_ids),
                            "role": spec.role,
                        }
                    )
                    for spec, candidate, license_basis, _ in rows
                ),
                key=lambda item: item.candidate_id,
            )
        ),
    )
    metadata_path = service.session_dir / "candidate-metadata.json"
    atomic_write_bytes(
        metadata_path,
        canonical_json(metadata.to_dict()).encode("utf-8") + b"\n",
        root=artifact_root,
        label="candidate metadata",
    )
    EvidenceImportMetadata.from_dict(
        json.loads(
            secure_read_bytes(
                metadata_path,
                root=artifact_root,
                label="candidate metadata",
            )
        )
    )
    return PreparationResult(
        review_path=review_path,
        metadata_path=metadata_path,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    arguments = parser.parse_args()
    result = prepare(arguments.artifact_root)
    print(result.review_path.name)
    print(result.metadata_path.name)
