from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from tracelane.evidence_registry import (
    EvidenceImportMetadata,
    EvidenceProject,
    import_acquisition_project,
    verify_evidence_registry,
)
from tracelane.v2.storage import secure_read_bytes

_SESSION_ID = "acq_hist001_20260724"
_REQUIRED_DOMAINS = frozenset(
    {
        "diplomacy",
        "economy",
        "iberia",
        "imperial-governance",
        "logistics",
        "military",
    }
)
_SOURCE_COUNTS = Counter(
    {
        "hist001_tilsit_treaty": 1,
        "hist001_continental_system_decrees": 2,
        "hist001_russian_trade_1811": 1,
        "hist001_napoleon_supply_correspondence": 1,
        "hist001_wellington_iberia_dispatch": 1,
        "hist001_french_conscription_1811": 2,
        "hist001_twenty_ninth_bulletin": 1,
    }
)
_SOURCE_SCOPES = {
    "hist001_tilsit_treaty": (
        frozenset({"diplomacy", "economy"}),
        frozenset(
            {
                "diplomacy.duchy_of_warsaw",
                "diplomacy.tilsit_settlement",
                "economy.british_trade_exclusion",
            }
        ),
        "evidence",
    ),
    "hist001_continental_system_decrees": (
        frozenset({"economy", "imperial-governance"}),
        frozenset(
            {
                "economy.continental_system_scope",
                "economy.neutral_shipping_exposure",
                "imperial_governance.allied_enforcement",
            }
        ),
        "evidence",
    ),
    "hist001_russian_trade_1811": (
        frozenset({"diplomacy", "economy"}),
        frozenset(
            {
                "diplomacy.franco_russian_trade_friction",
                "economy.russian_trade_rules_1811",
            }
        ),
        "evidence",
    ),
    "hist001_napoleon_supply_correspondence": (
        frozenset({"logistics", "military"}),
        frozenset(
            {
                "logistics.prewar_supply_plan",
                "military.niemen_consumption_boundary",
            }
        ),
        "evidence",
    ),
    "hist001_wellington_iberia_dispatch": (
        frozenset({"iberia"}),
        frozenset(
            {
                "iberia.allied_force_commitment",
                "iberia.portuguese_finance_and_supply",
            }
        ),
        "evidence",
    ),
    "hist001_french_conscription_1811": (
        frozenset({"imperial-governance", "military"}),
        frozenset(
            {
                "imperial_governance.reserve_and_department_allocation",
                "military.conscription_scale_1811",
            }
        ),
        "evidence",
    ),
    "hist001_twenty_ninth_bulletin": (
        frozenset({"military"}),
        frozenset({"military.post_campaign_outcome"}),
        "future-control",
    ),
}
_SOURCE_LICENSES = {
    "hist001_tilsit_treaty": (
        "Repository-authored paraphrase of a public-domain treaty and "
        "public-domain contemporary translation."
    ),
    "hist001_continental_system_decrees": (
        "Repository-authored paraphrase of public-domain decrees; the "
        "source identifies the historical editions and translations."
    ),
    "hist001_russian_trade_1811": (
        "Repository-authored paraphrase using the Presidential Library "
        "catalogue record for an 1810 State Council file; no archive image "
        "or modern anthology text is redistributed."
    ),
    "hist001_napoleon_supply_correspondence": (
        "Repository-authored paraphrase of public-domain Napoleonic "
        "correspondence; the modern page is used only as an archive locator "
        "and letter-number reference."
    ),
    "hist001_wellington_iberia_dispatch": (
        "Repository-authored paraphrase of a public-domain dispatch; no "
        "substantial verbatim text is redistributed."
    ),
    "hist001_french_conscription_1811": (
        "Repository-authored paraphrase of a public-domain proposed decree and tables."
    ),
    "hist001_twenty_ninth_bulletin": (
        "Repository-authored paraphrase of a public-domain military "
        "bulletin; retained only as a future-information leakage control."
    ),
}


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
    metadata_path = source / "acquisition" / _SESSION_ID / "candidate-metadata.json"
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
        source_counts = Counter(row.source_spec_id for row in metadata.candidates)
        covered_domains = {domain for row in metadata.candidates for domain in row.domains}
        if (
            metadata.project_id != "hist-001"
            or metadata.session_id != _SESSION_ID
            or len(metadata.candidates) != 9
            or source_counts != _SOURCE_COUNTS
            or covered_domains != _REQUIRED_DOMAINS
            or sum(row.role == "future-control" for row in metadata.candidates) != 1
            or any(
                row.source_type != "primary"
                or row.content_authorship != "repository_authored"
                or row.retention_policy != "paraphrase_only"
                or row.license_basis != _SOURCE_LICENSES[row.source_spec_id]
                or (
                    frozenset(row.domains),
                    frozenset(row.fact_ids),
                    row.role,
                )
                != _SOURCE_SCOPES[row.source_spec_id]
                for row in metadata.candidates
            )
        ):
            raise ValueError
        return metadata
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("HIST-001 import metadata is invalid") from exc


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
        report = import_acquisition_project(
            arguments.source,
            arguments.target,
            hist001_project(),
            metadata,
        )
        project_report = verify_evidence_registry(
            arguments.target,
            "hist-001",
        )
        registry_report = verify_evidence_registry(arguments.target)
        if (
            report.candidate_count != project_report.candidate_count
            or report.pending_count != project_report.status_counts["pending"]
            or report.future_control_count != project_report.future_control_count
            or report.project_index_sha256 != project_report.project_index_sha256
            or report.registry_sha256 != project_report.registry_sha256
            or report.registry_sha256 != registry_report.registry_sha256
        ):
            raise ValueError("HIST-001 persisted verification failed")
        print(
            f"project={report.project_id} "
            f"candidates={report.candidate_count} "
            f"pending={report.pending_count} "
            f"source_manifest_sha256={report.source_manifest_sha256} "
            f"project_index_sha256={report.project_index_sha256} "
            f"registry_sha256={report.registry_sha256}"
        )
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        print(
            "tracelane: error: HIST-001 evidence import failed",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
