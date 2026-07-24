from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tracelane.contracts import AgentAnswer


@dataclass(frozen=True)
class ClaimView:
    evidence_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def claim_views(answer: AgentAnswer | Mapping[str, object]) -> tuple[ClaimView, ...]:
    if isinstance(answer, AgentAnswer):
        return tuple(
            ClaimView(evidence_ids=claim.evidence_ids, fact_ids=claim.fact_ids)
            for claim in answer.claims
        )
    claims = answer.get("claims", ())
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        return ()
    return tuple(
        ClaimView(
            evidence_ids=_strings(claim.get("evidence_ids")),
            fact_ids=_strings(claim.get("fact_ids")),
        )
        for claim in claims
        if isinstance(claim, Mapping)
    )
