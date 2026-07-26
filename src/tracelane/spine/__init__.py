"""Decision → outcome → feedback spine for TraceLane runs.

This package adds the second half of the agent loop: after a run gathers,
analyzes, debates and finalizes, the spine commits a typed decision, resolves
it against the world into a factual outcome, and attributes the result into
deterministic feedback.  Every record is journaled to an append-only,
hash-chained ledger so the whole chain stays auditable and replayable.
"""

from tracelane.spine.contracts import (
    DecisionRecord,
    FeedbackRecord,
    OutcomeRecord,
    OutcomeStatus,
    SignalDirection,
    TypedSignal,
)
from tracelane.spine.debate import DebateDecision, DebatePolicy, should_debate
from tracelane.spine.feedback import (
    ReliabilityProposal,
    Resolution,
    attribute_feedback,
    propose_reliability_updates,
    resolve_decision,
    stance_from_score,
)
from tracelane.spine.fusion import FUSION_POLICY_VERSION, FusionResult, fuse_signals
from tracelane.spine.ledger import LEDGER_NAME, Ledger, LedgerEntry

__all__ = [
    "FUSION_POLICY_VERSION",
    "LEDGER_NAME",
    "DebateDecision",
    "DebatePolicy",
    "DecisionRecord",
    "FeedbackRecord",
    "FusionResult",
    "Ledger",
    "LedgerEntry",
    "OutcomeRecord",
    "OutcomeStatus",
    "ReliabilityProposal",
    "Resolution",
    "SignalDirection",
    "TypedSignal",
    "attribute_feedback",
    "fuse_signals",
    "propose_reliability_updates",
    "resolve_decision",
    "should_debate",
    "stance_from_score",
]
