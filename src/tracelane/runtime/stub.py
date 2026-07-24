from __future__ import annotations

from collections.abc import Mapping, Sequence

from tracelane.contracts import canonical_json
from tracelane.runtime.base import ModelRequest, ModelResponse


def _claims_from_evidence(request: ModelRequest) -> list[dict[str, object]]:
    claims: list[dict[str, object]] = []
    for record in sorted(
        request.evidence,
        key=lambda item: (item.available_at, item.evidence_id),
    ):
        claims.append(
            {
                "text": record.text,
                "evidence_ids": [record.evidence_id],
                "fact_ids": sorted(record.fact_ids),
            }
        )
    return claims


def _prior_claims(prior_output: Mapping[str, object]) -> list[dict[str, object]]:
    claims = prior_output.get("claims", [])
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        return []
    return [dict(claim) for claim in claims if isinstance(claim, Mapping)]


class DeterministicStubRuntime:
    """An offline model double that can only reason over supplied evidence."""

    model_id = "deterministic-stub-v1"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.stage == "analyze":
            content: Mapping[str, object] = {"claims": _claims_from_evidence(request)}
        elif request.stage == "debate":
            content = {
                "claims": _prior_claims(request.prior_output),
                "resolution": "stable-evidence-order",
            }
        elif request.stage == "finalize":
            claims = _prior_claims(request.prior_output)
            answer_parts = list(dict.fromkeys(str(claim["text"]) for claim in claims))
            content = {
                "answer": " ".join(answer_parts) or "Insufficient admitted evidence.",
                "claims": claims,
                "missing_information": [],
            }
        else:
            raise ValueError(f"stub runtime does not support stage: {request.stage}")

        request_payload = {
            "run_id": request.run_id,
            "stage": request.stage,
            "role": request.role,
            "question": request.question,
            "evidence": request.evidence,
            "prior_output": request.prior_output,
            "seed": request.seed,
        }
        input_tokens = max(1, (len(canonical_json(request_payload)) + 3) // 4)
        output_tokens = max(1, (len(canonical_json(content)) + 3) // 4)
        return ModelResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            latency_ms=1,
            attempt=1,
        )
