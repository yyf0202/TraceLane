from __future__ import annotations

from copy import deepcopy

from tracelane.contracts import AgentAnswer, Claim, load_task
from tracelane.evidence import freeze_evidence
from tracelane.graders.completion import grade_completion
from tracelane.graders.grounding import grade_grounding
from tracelane.graders.pit import grade_pit
from tracelane.graders.recovery import grade_recovery


def two_fact_task(*, fault_scenario: str | None = None):
    return load_task(
        {
            "task_id": "grader-001",
            "question": "What are the two verified conditions?",
            "cutoff_at": "2026-01-10T00:00:00Z",
            "expected_facts": {
                "fact-one": "Condition one is stable.",
                "fact-two": "Condition two is ready.",
            },
            "completion_facts": ["fact-one", "fact-two"],
            "evidence": [
                {
                    "evidence_id": "ev-one",
                    "available_at": "2026-01-09T00:00:00Z",
                    "source": "synthetic-note",
                    "text": "Condition one is stable.",
                    "fact_ids": ["fact-one"],
                },
                {
                    "evidence_id": "ev-two",
                    "available_at": "2026-01-09T01:00:00Z",
                    "source": "synthetic-note",
                    "text": "Condition two is ready.",
                    "fact_ids": ["fact-two"],
                },
                {
                    "evidence_id": "ev-future",
                    "available_at": "2026-01-10T00:00:01Z",
                    "source": "synthetic-note",
                    "text": "A future update.",
                    "fact_ids": ["fact-two"],
                },
            ],
            "future_evidence_ids": ["ev-future"],
            "fault_scenario": fault_scenario,
            "license": "CC0-1.0 synthetic",
        }
    )


def one_fact_answer() -> AgentAnswer:
    return AgentAnswer(
        answer="Condition one is stable.",
        claims=(
            Claim(
                text="Condition one is stable.",
                evidence_ids=("ev-one",),
                fact_ids=("fact-one",),
            ),
        ),
        missing_information=("fact-two",),
    )


def test_completion_one_of_two_is_half() -> None:
    grade = grade_completion(one_fact_answer(), two_fact_task())
    assert grade.covered_required_facts == 1
    assert grade.required_facts == 2
    assert grade.coverage == 0.5


def test_grounding_uses_hand_calculated_precision_and_recall() -> None:
    task = two_fact_task()
    bundle = freeze_evidence(task, task.evidence)
    answer = {
        "answer": "Two claims.",
        "claims": [
            {
                "text": "Condition one is stable.",
                "evidence_ids": ["ev-one"],
                "fact_ids": ["fact-one"],
            },
            {
                "text": "An unsupported condition.",
                "evidence_ids": ["ev-unknown"],
                "fact_ids": ["fact-two"],
            },
        ],
        "missing_information": [],
    }
    grade = grade_grounding(answer, task, bundle)
    assert grade.cited_claims == 2
    assert grade.supported_cited_claims == 1
    assert grade.citation_precision == 0.5
    assert grade.citation_recall == 0.5
    assert grade.unsupported_claims == 1


def test_claim_without_citations_is_unsupported() -> None:
    task = two_fact_task()
    bundle = freeze_evidence(task, task.evidence)
    value = deepcopy(
        {
            "answer": "Unsupported.",
            "claims": [
                {
                    "text": "Unsupported.",
                    "evidence_ids": [],
                    "fact_ids": ["fact-one"],
                }
            ],
            "missing_information": [],
        }
    )
    grade = grade_grounding(value, task, bundle)
    assert grade.cited_claims == 0
    assert grade.unsupported_claims == 1


def test_post_cutoff_citation_is_a_pit_violation() -> None:
    task = two_fact_task()
    answer = {
        "answer": "Future update.",
        "claims": [
            {
                "text": "Future update.",
                "evidence_ids": ["ev-future", "ev-future"],
                "fact_ids": ["fact-two"],
            }
        ],
        "missing_information": [],
    }
    grade = grade_pit(answer, task)
    assert grade.pit_violations == 1
    assert not grade.pit_safe


def test_recovery_succeeds_without_repeating_completed_stages() -> None:
    trace = [
        {"event_type": "stage.completed", "stage": "gather", "payload": {}},
        {"event_type": "stage.completed", "stage": "analyze", "payload": {}},
        {"event_type": "stage.failed", "stage": "finalize", "payload": {}},
        {
            "event_type": "run.resumed",
            "stage": None,
            "payload": {"checkpoint_stage": "analyze"},
        },
        {"event_type": "stage.started", "stage": "finalize", "payload": {}},
        {"event_type": "stage.completed", "stage": "finalize", "payload": {}},
    ]
    grade = grade_recovery(trace, two_fact_task(fault_scenario="fail-once-finalize"))
    assert grade.recovery_applicable
    assert grade.recovery_success is True
    assert grade.resume_position == "analyze"
    assert grade.repeated_completed_stages == ()


def test_no_fault_is_not_reported_as_perfect_recovery() -> None:
    grade = grade_recovery([], two_fact_task())
    assert not grade.recovery_applicable
    assert grade.recovery_success is None
