from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from tracelane.acquisition import EvidenceCandidate, ManualAcquisitionService
from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.contracts import canonical_json
from tracelane.evidence_registry import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
    import_acquisition_project,
)
from tracelane.hist001 import (
    HIST001_CURATOR,
    HIST001_RETRIEVED_AT,
    HIST001_SESSION_ID,
    HIST001_SOURCE_MANIFEST,
)
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.storage import secure_read_bytes

_REQUIRED_DOMAINS = frozenset(domain for spec in HIST001_SOURCE_MANIFEST for domain in spec.domains)


def hist001_project() -> EvidenceProject:
    return EvidenceProject.create(
        project_id="hist-001",
        title="Napoleon 1812 Counterfactual",
        research_question=(
            "How might European history have changed if Napoleon had not "
            "crossed the Niemen or launched the Russian campaign in 1812?"
        ),
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="Napoleon does not cross the Niemen or launch the Russian campaign.",
        required_domains=tuple(sorted(_REQUIRED_DOMAINS)),
        admitted_source_types=("primary",),
        status="active",
    )


def _read_metadata(source: Path) -> EvidenceImportMetadata:
    metadata_path = source / "acquisition" / HIST001_SESSION_ID / "candidate-metadata.json"
    try:
        value = json.loads(
            secure_read_bytes(
                metadata_path,
                root=source,
                label="HIST-001 import metadata",
            )
        )
        if not isinstance(value, dict):
            raise ValueError
        metadata = EvidenceImportMetadata.from_dict(value)
        if (
            metadata.project_id != "hist-001"
            or metadata.session_id != HIST001_SESSION_ID
            or len(metadata.candidates) != 9
        ):
            raise ValueError
        return metadata
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("HIST-001 import metadata is invalid") from exc


def _expected_locked_candidates() -> tuple[
    tuple[EvidenceCandidate, EvidenceImportRow, ArtifactRef, bytes, bytes],
    ...,
]:
    expected: list[tuple[EvidenceCandidate, EvidenceImportRow, ArtifactRef, bytes, bytes]] = []
    for spec in HIST001_SOURCE_MANIFEST:
        for document_date in spec.document_date.split("/"):
            date_precision = {
                4: "year",
                7: "month",
                10: "day",
            }[len(document_date)]
            content_bytes = spec.note.encode("utf-8")
            content_sha256 = hashlib.sha256(content_bytes).hexdigest()
            content_ref = ArtifactRef.from_dict(
                {
                    "kind": "evidence_blob",
                    "uri": (
                        "tracelane://artifacts/blobs/sha256/"
                        f"{content_sha256[:2]}/{content_sha256}.blob"
                    ),
                    "media_type": "text/plain",
                    "sha256": content_sha256,
                    "size_bytes": len(content_bytes),
                }
            )
            candidate_id = compute_candidate_id(
                query=spec.query,
                title=spec.title,
                source_url=spec.source_url,
                document_date=document_date,
                date_precision=date_precision,
                content_sha256=content_sha256,
            )
            candidate = EvidenceCandidate.create(
                candidate_id=candidate_id,
                query=spec.query,
                title=spec.title,
                source_url=spec.source_url,
                document_date=document_date,
                date_precision=date_precision,  # type: ignore[arg-type]
                retrieved_at=HIST001_RETRIEVED_AT,
                curator=HIST001_CURATOR,
                transformation_refs=(),
                content_ref=content_ref,
            )
            candidate_bytes = canonical_json(candidate.to_dict()).encode("utf-8") + b"\n"
            candidate_ref = ArtifactRef.from_dict(
                {
                    "kind": "evidence_candidate",
                    "uri": (
                        "tracelane://artifacts/acquisition/"
                        f"{HIST001_SESSION_ID}/candidates/{candidate_id}.json"
                    ),
                    "media_type": "application/json",
                    "schema_id": "tracelane://schemas/evidence-candidate/v2",
                    "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                    "size_bytes": len(candidate_bytes),
                }
            )
            row = EvidenceImportRow.from_dict(
                {
                    "source_spec_id": spec.source_spec_id,
                    "candidate_id": candidate.candidate_id,
                    "candidate_record_sha256": candidate.record_sha256,
                    "candidate_content_sha256": candidate.content_sha256,
                    "source_type": spec.source_type,
                    "license_basis": spec.license_basis,
                    "content_authorship": "repository_authored",
                    "retention_policy": "paraphrase_only",
                    "domains": sorted(spec.domains),
                    "fact_ids": sorted(spec.fact_ids),
                    "role": spec.role,
                }
            )
            expected.append(
                (
                    candidate,
                    row,
                    candidate_ref,
                    candidate_bytes,
                    content_bytes,
                )
            )
    return tuple(sorted(expected, key=lambda item: item[0].candidate_id))


def _authenticate_locked_package(
    source: Path,
    metadata: EvidenceImportMetadata,
) -> None:
    try:
        service = ManualAcquisitionService(
            source,
            session_id=HIST001_SESSION_ID,
        )
        closures = service.snapshot_candidates()
        expected = _expected_locked_candidates()
        if len(closures) != len(expected):
            raise ValueError
        manifest_value: dict[str, object] = {
            "schema_id": "tracelane://schemas/acquisition-session/v2",
            "schema_version": "2.0.0",
            "content_sha256": "",
            "session_id": HIST001_SESSION_ID,
            "mode": "codex_manual",
            "created_at": HIST001_RETRIEVED_AT,
            "network_access_available_to_agent": False,
            "candidate_refs": [item[2].to_dict() for item in expected],
            "review_refs": [],
            "promoted_record_refs": [],
        }
        if metadata.manifest_sha256 != content_digest(manifest_value):
            raise ValueError
        closure_by_id = {closure.candidate.candidate_id: closure for closure in closures}
        expected_rows: list[EvidenceImportRow] = []
        for candidate, row, candidate_ref, candidate_bytes, content_bytes in expected:
            closure = closure_by_id.get(candidate.candidate_id)
            if (
                closure is None
                or closure.candidate != candidate
                or closure.candidate_ref != candidate_ref
                or closure.candidate_bytes != candidate_bytes
                or closure.content_bytes != content_bytes
                or closure.transformations
            ):
                raise ValueError
            expected_rows.append(row)
        if metadata.candidates != tuple(expected_rows):
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError("HIST-001 candidate package is invalid") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import the authenticated HIST-001 candidate package."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path("evidence"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        metadata = _read_metadata(arguments.source)
        _authenticate_locked_package(arguments.source, metadata)
        report = import_acquisition_project(
            arguments.source,
            arguments.target,
            hist001_project(),
            metadata,
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        print(
            "tracelane: error: HIST-001 evidence import failed",
            file=sys.stderr,
        )
        return 1
    with suppress(OSError, ValueError):
        print(
            f"project={report.project_id} "
            f"candidates={report.candidate_count} "
            f"pending={report.pending_count} "
            f"source_manifest_sha256={report.source_manifest_sha256} "
            f"project_index_sha256={report.project_index_sha256} "
            f"registry_sha256={report.registry_sha256}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
