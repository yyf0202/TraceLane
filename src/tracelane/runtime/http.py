"""A real OpenAI-compatible HTTP model runtime.

This runtime drives the same :class:`ModelRequest` / :class:`ModelResponse`
contract as the deterministic stub, but against a live OpenAI-compatible chat
endpoint (for example a Volcengine Ark Coding Plan ``/api/coding/v3`` base
URL).  It keeps the harness's guarantees intact while talking to a real model:

* the API key is read from a private local config file, never hard-coded, and
  is never written into traces, manifests, or artifacts;
* the model is asked for a JSON object; the reply is parsed back into the
  ``Mapping`` content the orchestrator expects, with a clear error on
  malformed output;
* token usage is read from the provider's ``usage`` block so operational
  graders see real costs;
* requests are deterministic-by-construction (fixed seed, ``temperature=0``)
  and retried a bounded number of times on transient failures.

The runtime never logs or echoes the key.  Only the request/response *shape*
is shared with the endpoint.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import canonical_json
from tracelane.runtime.base import ModelRequest, ModelResponse


@dataclass(frozen=True)
class HttpRuntimeConfig:
    """Connection parameters for an OpenAI-compatible endpoint."""

    base_url: str
    api_key: str
    model_id: str
    timeout_seconds: float = 120.0
    max_retries: int = 2
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")


_SYSTEM_PROMPT = (
    "You are one stage of an evidence-grounded research harness. "
    "Respond with a single JSON object only — no prose, no markdown fences. "
    "Ground every statement in the supplied evidence and cite evidence ids."
)

_STAGE_INSTRUCTIONS: dict[str, str] = {
    "analyst": (
        "You are an analyst producing a typed signal. Return a JSON object with "
        'keys: "direction" (one of "bullish", "bearish", "neutral"), '
        '"confidence" (0..1), "evidence_ids" (array of cited evidence ids), '
        '"abstained" (boolean), "abstain_reason" (string or null). '
        'If you have no usable evidence, set abstained=true, direction="abstain", '
        "confidence=0, and explain in abstain_reason. Otherwise cite at least one "
        "evidence id and keep confidence above 0."
    ),
    "analyze": (
        "Extract the grounded claims from the evidence. Return a JSON object with "
        'a "claims" array; each claim has "text", "evidence_ids", and "fact_ids" '
        "arrays."
    ),
    "debate": (
        "Reconcile the prior analysis against conflicting evidence. Return a JSON "
        'object with "claims" (same shape as analyze) and a "resolution" string.'
    ),
    "finalize": (
        "Write the final grounded answer. Return a JSON object with "
        '"answer" (string), "claims" (array with "text", "evidence_ids", '
        '"fact_ids"), and "missing_information" (array of strings).'
    ),
}


def _build_messages(request: ModelRequest) -> list[dict[str, str]]:
    stage_instruction = _STAGE_INSTRUCTIONS.get(
        request.stage,
        "Return a JSON object appropriate for this stage.",
    )
    evidence_lines = [
        {
            "evidence_id": record.evidence_id,
            "available_at": record.available_at.isoformat(),
            "source": record.source,
            "text": record.text,
            "fact_ids": list(record.fact_ids),
        }
        for record in request.evidence
    ]
    user_payload = {
        "question": request.question,
        "role": request.role,
        "evidence": evidence_lines,
        "prior_output": request.prior_output,
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"{stage_instruction}\n\nInput JSON:\n{canonical_json(user_payload)}",
        },
    ]


def _extract_json_object(text: str) -> Mapping[str, object]:
    """Parse a JSON object from a model reply, tolerating code fences and prose.

    Real models variously wrap JSON in markdown fences, prepend/append prose,
    or emit nested objects, so a naive first-brace/last-brace slice is fragile.
    We try a strict parse first, then a precise ``raw_decode`` from the first
    opening brace, which handles nesting and trailing content correctly.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = [line for line in candidate.splitlines() if not line.strip().startswith("```")]
        candidate = "\n".join(lines).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, Mapping):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    if start == -1:
        raise ValueError("model reply did not contain a JSON object")
    decoder = json.JSONDecoder()
    try:
        parsed, _end = decoder.raw_decode(candidate, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model reply JSON could not be parsed: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("model reply JSON is not an object")
    return parsed


class OpenAICompatibleRuntime:
    """Drive the model contract against a live OpenAI-compatible endpoint."""

    def __init__(self, config: HttpRuntimeConfig) -> None:
        self._config = config

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def _endpoint(self) -> str:
        base = self._config.base_url.rstrip("/")
        return f"{base}/chat/completions"

    def _request_body(self, request: ModelRequest) -> bytes:
        body = {
            "model": self._config.model_id,
            "messages": _build_messages(request),
            "temperature": self._config.temperature,
            "seed": request.seed,
            "response_format": {"type": "json_object"},
        }
        return json.dumps(body).encode("utf-8")

    def _post(self, payload: bytes) -> Mapping[str, object]:
        http_request = urllib.request.Request(
            self._endpoint(),
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
        )
        with urllib.request.urlopen(  # noqa: S310 - endpoint is operator-configured
            http_request, timeout=self._config.timeout_seconds
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = self._request_body(request)
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                body = self._post(payload)
                content_text = self._message_text(body)
                content = _extract_json_object(content_text)
                latency_ms = int((time.monotonic() - started) * 1000)
                usage = body.get("usage", {}) if isinstance(body, Mapping) else {}
                if not isinstance(usage, Mapping):
                    usage = {}
                return ModelResponse(
                    content=content,
                    input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(usage.get("completion_tokens", 0) or 0),
                    cached_tokens=int(
                        usage.get("prompt_cache_hit_tokens", 0)
                        or usage.get("cached_tokens", 0)
                        or 0
                    ),
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
            except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(min(2.0**attempt, 8.0))
        raise ValueError(
            f"model request failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _message_text(body: Mapping[str, object]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("response contained no choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ValueError("response choice is invalid")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("response choice has no message")
        content = message.get("content")
        if isinstance(content, str):
            return content
        # Some providers return a list of content parts.
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            ]
            return "".join(str(p) for p in parts)
        raise ValueError("response message content is invalid")
