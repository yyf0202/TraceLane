from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tracelane.acquisition import EvidenceCandidate, ManualAcquisitionService
from tracelane.contracts import canonical_json
from tracelane.evidence_registry import EvidenceImportMetadata, EvidenceImportRow
from tracelane.security import classify_and_redact
from tracelane.v2.contracts import content_digest
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import atomic_write_bytes, secure_read_bytes


@dataclass(frozen=True)
class CandidateSpec:
    source_spec_id: str
    query: str
    title: str
    source_url: str
    document_date: str
    source_type: str
    license_basis: str
    domains: tuple[str, ...]
    fact_ids: tuple[str, ...]
    note: str
    role: str = "evidence"


@dataclass(frozen=True)
class PreparationResult:
    review_path: Path
    metadata_path: Path


SPECS = (
    CandidateSpec(
        source_spec_id="hist001_tilsit_treaty",
        query='"Treaty of Tilsit" 1807 full text public domain',
        title="Treaty of Tilsit, 9 July 1807",
        source_url=("https://en.wikisource.org/wiki/Treaty_of_Tilsit%2C_9_July_1807"),
        document_date="1807-07-09",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of a public-domain treaty and "
            "public-domain contemporary translation."
        ),
        domains=("diplomacy", "economy"),
        fact_ids=(
            "diplomacy.tilsit_settlement",
            "diplomacy.duchy_of_warsaw",
            "economy.british_trade_exclusion",
        ),
        note=(
            "The public treaty records the post-1807 settlement between France "
            "and Prussia. It recognizes Napoleon's allied and satellite states, "
            "creates the Duchy of Warsaw from former Prussian Polish lands, and "
            "closes Danzig and Prussian-controlled ports to British trade during "
            "the maritime war. This evidence establishes the diplomatic and "
            "commercial architecture inherited by the 1812 decision; it does not "
            "predict what would happen after a different decision."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_continental_system_decrees",
        query='"Berlin Decree" "Milan Decree" full text public domain',
        title="Documents upon the Continental System: Berlin and Milan Decrees",
        source_url=(
            "https://www.napoleon-series.org/research/government/diplomatic/c_continental.html"
        ),
        document_date="1806-11-21/1807-12-17",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of public-domain decrees; the "
            "source identifies the historical editions and translations."
        ),
        domains=("economy", "imperial-governance"),
        fact_ids=(
            "economy.continental_system_scope",
            "economy.neutral_shipping_exposure",
            "imperial_governance.allied_enforcement",
        ),
        note=(
            "The Berlin Decree declared the British Isles blockaded, prohibited "
            "commerce and correspondence with them, treated British goods as "
            "lawful prize, and required French ministers and allied governments "
            "to enforce the system. The Milan Decree escalated this policy by "
            "treating neutral vessels that submitted to British search or called "
            "at British ports as denationalized and subject to seizure. Together "
            "they show that enforcing the Continental System imposed governance "
            "and diplomatic costs across Napoleon's allied network."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_russian_trade_1811",
        query="Russia tariff decree 1810 continental system primary source",
        title="Russian arrangements for foreign trade in 1811",
        source_url="https://www.prlib.ru/item/330801?mode=rusmarc",
        document_date="1810-12-19",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase using the Presidential Library "
            "catalogue record for an 1810 State Council file; no archive image "
            "or modern anthology text is redistributed."
        ),
        domains=("diplomacy", "economy"),
        fact_ids=(
            "economy.russian_trade_rules_1811",
            "diplomacy.franco_russian_trade_friction",
        ),
        note=(
            "The Presidential Library catalogue identifies the State Council "
            "file on foreign-trade arrangements for 1811, opened in late 1810 "
            "and approved on 19 December 1810. A companion transcription locator "
            "(https://ido.tsu.ru/other_res/hischool/soslov/pred.htm) identifies "
            "the measure as the Regulation on Neutral Trade for 1811 and cites "
            "the Complete Collection of Laws of the Russian Empire, volume 31, "
            "number 24456. The record establishes that Russia had formally revised "
            "its trade regime before the 1812 decision."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_napoleon_supply_correspondence",
        query=("Napoleon correspondence supplies magazines 1812 before June full text"),
        title="Napoleon's pre-campaign correspondence on supplies, March 1812",
        source_url=(
            "https://www.napoleon.org/histoire-des-2-empires/articles/"
            "1812-la-campagne-de-russie-preface-de-mp-rey-au-tome-12-de-la-"
            "correspondance-generale-de-napoleon-bonaparte/"
        ),
        document_date="1812-03-26",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of public-domain Napoleonic "
            "correspondence; the modern page is used only as an archive locator "
            "and letter-number reference."
        ),
        domains=("logistics", "military"),
        fact_ids=(
            "logistics.prewar_supply_plan",
            "military.niemen_consumption_boundary",
        ),
        note=(
            "The source indexes Napoleon's 1812 correspondence and identifies "
            "letter no. 30301 to Berthier, dated 26 March 1812. It reports a "
            "specific supply rule: troops should use local resources before the "
            "Niemen and preserve carried provisions for consumption only after "
            "crossing. Other pre-campaign orders fixed stocks, clothing and route "
            "schedules. This establishes the intended logistics model known before "
            "the decision cutoff, without importing later campaign outcomes."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_wellington_iberia_dispatch",
        query="Wellington dispatch Peninsular War 1811 full text",
        title="Wellington to Liverpool, Villa Fermosa, 7 May 1811",
        source_url="https://www.wtj.com/archives/wellington/1811_05b.htm",
        document_date="1811-05-07",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of a public-domain dispatch; no "
            "substantial verbatim text is redistributed."
        ),
        domains=("iberia",),
        fact_ids=(
            "iberia.allied_force_commitment",
            "iberia.portuguese_finance_and_supply",
        ),
        note=(
            "Wellington's dispatch describes reduced Portuguese force strength, "
            "the Portuguese government's shortage of money and difficulty paying "
            "for supplies in Spain, and the British force needed merely to hold "
            "Portugal. It is direct pre-cutoff evidence that the Iberian war tied "
            "down troops, subsidies, supplies and administrative attention. It "
            "does not assume that redeploying French resources would automatically "
            "produce a decisive result."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_french_conscription_1811",
        query=("French conscription allied contingents decree 1811 1812 primary source"),
        title="Council of State recommendation on the 1811 conscription",
        source_url=(
            "https://www.napoleon-series.org/military-info/organization/France/"
            "Conscription/1811/c_conscripts1811.html"
        ),
        document_date="1810-12-13/1811",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of a public-domain proposed decree and tables."
        ),
        domains=("imperial-governance", "military"),
        fact_ids=(
            "military.conscription_scale_1811",
            "imperial_governance.reserve_and_department_allocation",
        ),
        note=(
            "The proposed decree states that the authorized 1811 levy comprised "
            "120,000 conscripts, with 80,000 to be activated and the remainder "
            "held as reserve; maritime cantons were to provide conscripts to the "
            "navy. Its associated tables allocate men across departments and "
            "incorporated territories. This supports analysis of the manpower and "
            "administrative demands of maintaining a multi-theatre empire before "
            "the Russian campaign."
        ),
    ),
    CandidateSpec(
        source_spec_id="hist001_twenty_ninth_bulletin",
        query="Napoleon 29th bulletin December 1812 full text public domain",
        title="29th Bulletin of the Grande Armée, 3 December 1812",
        source_url=(
            "https://www.napoleon.org/histoire-des-2-empires/articles/"
            "29e-bulletin-de-la-grande-armee-molodetchna-3-decembre-1812/"
        ),
        document_date="1812-12-03",
        source_type="primary",
        license_basis=(
            "Repository-authored paraphrase of a public-domain military "
            "bulletin; retained only as a future-information leakage control."
        ),
        domains=("military",),
        fact_ids=("military.post_campaign_outcome",),
        note=(
            "The bulletin reports events and conditions during the retreat, "
            "including combat around the Berezina and the deteriorated state of "
            "the campaign. Because it was created months after the 23 June 1812 "
            "decision cutoff, it must never enter the agent's point-in-time "
            "evidence. It is retained only to test whether the harness detects and "
            "rejects future-information leakage."
        ),
        role="future-control",
    ),
)


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
        session_id="acq_hist001_20260724",
        clock=lambda: datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
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
                curator="codex-manual",
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
    for index, (spec, candidate, license_basis, note) in enumerate(rows, start=1):
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
    return PreparationResult(review_path=review_path, metadata_path=metadata_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    arguments = parser.parse_args()
    result = prepare(arguments.artifact_root)
    print(result.review_path.name)
    print(result.metadata_path.name)
