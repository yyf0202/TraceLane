"""Distill one real TradingAgents research run into a sanitized TraceLane showcase.

This is an offline, deterministic adapter.  It reads one ``fullflow`` JSON
artifact — the trace of a real TradingAgents multi-agent research run (analyst
reports, a bull/bear debate, a risk debate, and a final rating) — and emits a
standard TraceLane decision-suite task under ``fixtures/showcase/``.  The
structure of a real research agent's investigation becomes a reproducible
spine showcase without copying any real tickers, company names, or report
prose into the repository.

What is preserved (the *structure* of the investigation):

* the analyst roster that actually ran;
* a deterministic per-analyst direction/confidence derived from structural
  signals (report sizes, the bull/bear debate balance, the final rating);
* a point-in-time resolution mapped from the run's final rating.

What is deliberately dropped (the *content*):

* the real ticker / company identity (replaced by a synthetic subject id);
* every analyst report's prose (replaced by a short synthetic evidence note
  tagged only with its source type, e.g. ``market-report``).

Determinism: identical input JSON produces byte-identical output.  No model is
called and no wall clock is read, so the showcase is reproducible offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

# Map a research-graph analyst id to its report field and a spine role.
_ANALYST_REPORTS: dict[str, tuple[str, str]] = {
    "market": ("market_report", "market-analyst"),
    "social": ("sentiment_report", "sentiment-analyst"),
    "news": ("news_report", "news-analyst"),
    "fundamentals": ("fundamentals_report", "fundamentals-analyst"),
    "industry": ("industry_report", "industry-analyst"),
}

_RATING_DIRECTION: dict[str, str] = {
    "strong buy": "bullish",
    "buy": "bullish",
    "overweight": "bullish",
    "hold": "neutral",
    "sell": "bearish",
    "underweight": "bearish",
    "strong sell": "bearish",
}

_LICENSE = "CC0-1.0 synthetic (structure distilled from a real run)"


def _report_lengths(state: Mapping[str, object], analysts: Sequence[str]) -> dict[str, int]:
    lengths: dict[str, int] = {}
    for analyst in analysts:
        field = _ANALYST_REPORTS.get(analyst, (f"{analyst}_report", ""))[0]
        report = state.get(field, "")
        lengths[analyst] = len(report) if isinstance(report, str) else 0
    return lengths


def _direction_for(
    final_direction: str,
    bull_chars: int,
    bear_chars: int,
) -> str:
    """Consensus direction for a decisive (non-neutral) run.

    The aggregate debate balance tilts the roster toward or against the final
    stance.  This is a structural approximation, not a parse of the prose, and
    it is fully deterministic.
    """
    debate_total = bull_chars + bear_chars
    bull_share = (bull_chars / debate_total) if debate_total else 0.5
    if final_direction == "bullish":
        return "bullish" if bull_share >= 0.4 else "bearish"
    # bearish final
    return "bearish" if bull_share <= 0.6 else "bullish"


def _balanced_split(items: Sequence[tuple[str, float]]) -> dict[str, str]:
    """Split a roster into bull/bear to balance confidence-weighted sums.

    A Hold is structurally a *standoff*: the desk's conviction cancels out.
    Greedy deterministic number-partitioning assigns each analyst to the
    currently lighter side so the fused score lands near zero, reproducing that
    standoff instead of fabricating a one-sided lean.
    """
    directions: dict[str, str] = {}
    bull_weight = 0.0
    bear_weight = 0.0
    for analyst_id, weight in sorted(items, key=lambda item: (-item[1], item[0])):
        if bull_weight <= bear_weight:
            directions[analyst_id] = "bullish"
            bull_weight += weight
        else:
            directions[analyst_id] = "bearish"
            bear_weight += weight
    return directions


def _confidence_for(report_chars: int, max_chars: int) -> float:
    """Confidence scales with how much the analyst produced (deterministic)."""
    if max_chars <= 0:
        return 0.5
    ratio = report_chars / max_chars
    return round(0.5 + 0.4 * min(1.0, ratio), 3)


def distill(fullflow: Mapping[str, object], subject_id: str) -> dict[str, object]:
    """Convert one fullflow artifact into a decision-suite task document."""
    state = fullflow.get("state", {})
    if not isinstance(state, Mapping):
        raise ValueError("fullflow artifact is missing its state")
    selected = fullflow.get("selected_analysts", [])
    if not isinstance(selected, list) or not selected:
        raise ValueError("fullflow artifact has no selected_analysts")
    analysts = [str(a) for a in selected]

    decision_text = str(fullflow.get("decision", "")).strip().lower()
    final_direction = _RATING_DIRECTION.get(decision_text, "neutral")

    debate = state.get("investment_debate", {})
    if not isinstance(debate, Mapping):
        debate = {}
    bull_chars = len(str(debate.get("bull_history", "")))
    bear_chars = len(str(debate.get("bear_history", "")))

    lengths = _report_lengths(state, analysts)
    max_chars = max(lengths.values(), default=0)

    confidences = {a: _confidence_for(lengths[a], max_chars) for a in analysts}
    if final_direction == "neutral":
        # A hold is a standoff: split the roster so weighted conviction cancels.
        directions = _balanced_split([(a, confidences[a]) for a in analysts])
    else:
        directions = {a: _direction_for(final_direction, bull_chars, bear_chars) for a in analysts}

    trade_date = str(state.get("trade_date", "2026-01-01"))
    cutoff = f"{trade_date}T15:00:00+08:00"

    analyst_specs = []
    evidence = []
    for index, analyst in enumerate(analysts, start=1):
        role = _ANALYST_REPORTS.get(analyst, (f"{analyst}_report", f"{analyst}-analyst"))[1]
        direction = directions[analyst]
        confidence = confidences[analyst]
        analyst_specs.append(
            {
                "analyst_id": analyst,
                "role": role,
                "direction_hint": direction,
                "confidence_hint": confidence,
            }
        )
        source_type = _ANALYST_REPORTS.get(analyst, ("report", ""))[0].replace("_", "-")
        evidence.append(
            {
                "available_at": f"{trade_date}T{8 + index:02d}:00:00+08:00",
                "evidence_id": f"{subject_id.lower()}-ev-{index:02d}",
                "fact_ids": [f"fact-{analyst}"],
                "source": f"tracelane-showcase-{source_type}",
                "text": (
                    f"Synthetic {analyst} note for showcase subject {subject_id}: "
                    f"structure distilled from a real {role} report."
                ),
            }
        )

    expected_facts = {
        f"fact-{analyst}": f"Structural contribution of the {analyst} analyst."
        for analyst in analysts
    }
    metric = {"bullish": 0.02, "bearish": -0.02, "neutral": 0.0}[final_direction]

    return {
        "analysts": analyst_specs,
        "resolution": {
            "actual_direction": final_direction,
            "metric_name": "net_alpha",
            "metric_value": metric,
        },
        "completion_facts": [f"fact-{analysts[0]}"],
        "cutoff_at": cutoff,
        "evidence": evidence,
        "expected_facts": expected_facts,
        "fault_scenario": None,
        "future_evidence_ids": [],
        "license": _LICENSE,
        "question": (
            f"Showcase subject {subject_id}: reconcile the analyst roster into a stance. "
            "Structure distilled from a real deep-research run."
        ),
        "task_id": subject_id,
    }


def _subject_id(fullflow: Mapping[str, object], index: int) -> str:
    """Derive a stable synthetic subject id from the run, never the ticker."""
    digest = (
        hashlib.sha256(
            json.dumps(
                [fullflow.get("date"), fullflow.get("selected_analysts"), fullflow.get("decision")],
                sort_keys=True,
            ).encode("utf-8")
        )
        .hexdigest()[:6]
        .upper()
    )
    return f"SHOWCASE-{index:03d}-{digest}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fullflow", type=Path, nargs="+", help="TradingAgents fullflow JSON trace(s)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("fixtures/showcase"),
        help="Output directory for showcase decision-suite tasks",
    )
    parser.add_argument(
        "--start-index", type=int, default=1, help="Starting showcase subject index"
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for offset, path in enumerate(args.fullflow):
        try:
            fullflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"cannot read fullflow artifact {path}: {exc}") from exc
        if not isinstance(fullflow, Mapping):
            raise SystemExit(f"fullflow artifact must be a JSON object: {path}")
        subject = _subject_id(fullflow, args.start_index + offset)
        task = distill(fullflow, subject)
        target = args.out / f"{subject.lower()}.json"
        payload = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        target.write_text(payload, encoding="utf-8")
        print(f"wrote {target} (subject={subject}, rating={fullflow.get('decision')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
