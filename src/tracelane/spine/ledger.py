"""Append-only, hash-chained audit ledger for spine records.

Every spine record (signal, decision, outcome, feedback) is journaled here as
an immutable envelope carrying the record plus a link to the previous
envelope's hash.  This mirrors the :class:`~tracelane.checkpoint.Checkpoint`
chain: the genesis envelope has ``previous_sha256 = None`` and each subsequent
envelope commits to its predecessor, so any tampering, reordering, or deletion
is detectable on read.

The ledger also enforces cross-record referential integrity: a decision may
only reference signal ids already journaled, an outcome only a journaled
decision, and feedback only a journaled decision/outcome pair.  This is what
makes "signals must rest on retained evidence" auditable end to end.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tracelane.artifacts import RunStore
from tracelane.contracts import sha256_json
from tracelane.spine.contracts import (
    RECORD_ID_FIELD,
    DecisionRecord,
    FeedbackRecord,
    OutcomeRecord,
)

LEDGER_NAME = "spine/ledger.jsonl"

_KINDS = ("signal", "decision", "outcome", "feedback")


@dataclass(frozen=True)
class LedgerEntry:
    """One journaled envelope: a record plus its hash-chain link."""

    sequence: int
    kind: str
    record_id: str
    record: object
    record_sha256: str
    previous_sha256: str | None
    entry_sha256: str

    def content_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "previous_sha256": self.previous_sha256,
            "record": self.record,
            "record_id": self.record_id,
            "record_sha256": self.record_sha256,
            "sequence": self.sequence,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.content_dict(), "entry_sha256": self.entry_sha256}


def _record_to_dict(kind: str, record: object) -> dict[str, object]:
    if not hasattr(record, "to_dict"):
        raise ValueError(f"{kind} record is not a spine contract")
    return record.to_dict()


class Ledger:
    """Append and verify the spine ledger for a single run."""

    def __init__(self, store: RunStore) -> None:
        self._store = store

    def _path(self) -> Path:
        return self._store.path_for(LEDGER_NAME)

    def _read_entries(self) -> list[LedgerEntry]:
        path = self._path()
        if not path.exists():
            return []
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("ledger contains invalid JSONL") from exc
        entries: list[LedgerEntry] = []
        previous: LedgerEntry | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            entry = self._parse_entry(row)
            if entry.sequence != expected_sequence:
                raise ValueError("ledger sequence is not contiguous")
            expected_previous = previous.entry_sha256 if previous is not None else None
            if entry.previous_sha256 != expected_previous:
                raise ValueError("ledger hash chain is broken")
            if entry.record_sha256 != sha256_json(entry.record):
                raise ValueError("ledger record hash does not match its content")
            if entry.entry_sha256 != sha256_json(entry.content_dict()):
                raise ValueError("ledger entry hash does not match its content")
            entries.append(entry)
            previous = entry
        return entries

    @staticmethod
    def _parse_entry(value: object) -> LedgerEntry:
        if not isinstance(value, dict):
            raise ValueError("ledger entry must be a JSON object")
        expected_keys = {
            "sequence",
            "kind",
            "record_id",
            "record",
            "record_sha256",
            "previous_sha256",
            "entry_sha256",
        }
        if set(value) != expected_keys:
            raise ValueError("ledger entry fields are invalid")
        sequence = value["sequence"]
        kind = value["kind"]
        record_id = value["record_id"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("ledger sequence is invalid")
        if kind not in _KINDS:
            raise ValueError("ledger kind is invalid")
        if not isinstance(record_id, str):
            raise ValueError("ledger record_id is invalid")
        if not isinstance(value["record_sha256"], str):
            raise ValueError("ledger record hash is invalid")
        previous = value["previous_sha256"]
        if previous is not None and not isinstance(previous, str):
            raise ValueError("ledger previous hash is invalid")
        if not isinstance(value["entry_sha256"], str):
            raise ValueError("ledger entry hash is invalid")
        return LedgerEntry(
            sequence=sequence,
            kind=kind,
            record_id=record_id,
            record=value["record"],
            record_sha256=value["record_sha256"],
            previous_sha256=previous,
            entry_sha256=value["entry_sha256"],
        )

    def _latest(self) -> LedgerEntry | None:
        entries = self._read_entries()
        return entries[-1] if entries else None

    def append(self, kind: str, record: object) -> LedgerEntry:
        """Append one spine record and return its journaled envelope."""
        if kind not in _KINDS:
            raise ValueError(f"unknown record kind: {kind}")
        record_dict = _record_to_dict(kind, record)
        id_field = RECORD_ID_FIELD[kind]
        record_id = record_dict.get(id_field)
        if not isinstance(record_id, str):
            raise ValueError(f"{kind} record is missing its id")
        self._check_references(kind, record)
        latest = self._latest()
        sequence = latest.sequence + 1 if latest is not None else 1
        previous_sha256 = latest.entry_sha256 if latest is not None else None
        record_sha256 = sha256_json(record_dict)
        entry = LedgerEntry(
            sequence=sequence,
            kind=kind,
            record_id=record_id,
            record=record_dict,
            record_sha256=record_sha256,
            previous_sha256=previous_sha256,
            entry_sha256="",
        )
        entry = LedgerEntry(**{**entry.__dict__, "entry_sha256": sha256_json(entry.content_dict())})
        self._store.append_jsonl(LEDGER_NAME, entry.to_dict())
        return entry

    def _check_references(self, kind: str, record: object) -> None:
        """Enforce that a record only references ids already journaled."""
        if kind == "signal":
            return
        entries = self._read_entries()
        seen: dict[str, set[str]] = {known: set() for known in _KINDS}
        for entry in entries:
            seen[entry.kind].add(entry.record_id)
        if kind == "decision":
            assert isinstance(record, DecisionRecord)
            unknown = set(record.signal_ids) - seen["signal"]
            if unknown:
                raise ValueError(f"decision references unknown signals: {sorted(unknown)}")
        elif kind == "outcome":
            assert isinstance(record, OutcomeRecord)
            if record.decision_id not in seen["decision"]:
                raise ValueError("outcome references an unknown decision")
        elif kind == "feedback":
            assert isinstance(record, FeedbackRecord)
            if record.decision_id not in seen["decision"]:
                raise ValueError("feedback references an unknown decision")
            if record.outcome_id not in seen["outcome"]:
                raise ValueError("feedback references an unknown outcome")
            unknown_signals = set(record.per_signal_verdicts) - seen["signal"]
            if unknown_signals:
                raise ValueError(f"feedback references unknown signals: {sorted(unknown_signals)}")

    def entries(self, kind: str | None = None) -> tuple[LedgerEntry, ...]:
        """Return verified entries, optionally filtered by record kind."""
        entries = self._read_entries()
        if kind is None:
            return tuple(entries)
        if kind not in _KINDS:
            raise ValueError(f"unknown record kind: {kind}")
        return tuple(entry for entry in entries if entry.kind == kind)

    def records(self, kind: str) -> tuple[Mapping[str, object], ...]:
        """Return the journaled record payloads for one kind, verified."""
        return tuple(entry.record for entry in self.entries(kind))
