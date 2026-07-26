"""A switchable, ablatable debate policy over the fused stance.

Debate is a first-class policy, not an always-on stage.  This module decides
*whether* a run should route its fused stance through a multi-perspective
debate before finalizing, based on measurable properties of the fusion output
(disagreement, coverage, abstention).  Keeping the trigger deterministic means
``always`` / ``conditional`` / ``never`` arms can be compared cleanly in an
ablation, and the conditional arm's threshold can itself be a study variable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tracelane.spine.fusion import FusionResult

DebatePolicy = Literal["always", "conditional", "never"]


@dataclass(frozen=True)
class DebateDecision:
    """The outcome of a debate-policy evaluation."""

    should_debate: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"reason": self.reason, "should_debate": self.should_debate}


def should_debate(
    fusion: FusionResult,
    *,
    policy: DebatePolicy = "conditional",
    disagreement_threshold: float = 0.5,
    coverage_threshold: float = 0.5,
) -> DebateDecision:
    """Decide whether to route a fused stance through debate.

    * ``always`` debates every run.
    * ``never`` never debates (the fused stance goes straight to finalize).
    * ``conditional`` debates when the fused stance is untrustworthy: the
      analysts abstained entirely, the surviving signals disagree, or too few
      analysts contributed evidence for the stance to be well-supported.
    """
    if policy not in ("always", "conditional", "never"):
        raise ValueError("policy is invalid")
    if not (0.0 <= disagreement_threshold <= 1.0):
        raise ValueError("disagreement_threshold must be within [0, 1]")
    if not (0.0 <= coverage_threshold <= 1.0):
        raise ValueError("coverage_threshold must be within [0, 1]")

    if policy == "always":
        return DebateDecision(should_debate=True, reason="policy_always")
    if policy == "never":
        return DebateDecision(should_debate=False, reason="policy_never")

    if fusion.direction == "abstain":
        return DebateDecision(should_debate=True, reason="fusion_abstained")
    if fusion.disagreement >= disagreement_threshold:
        return DebateDecision(
            should_debate=True,
            reason=f"disagreement {fusion.disagreement} >= {disagreement_threshold}",
        )
    if fusion.coverage < coverage_threshold:
        return DebateDecision(
            should_debate=True,
            reason=f"coverage {fusion.coverage} < {coverage_threshold}",
        )
    return DebateDecision(should_debate=False, reason="fusion_consensus")
