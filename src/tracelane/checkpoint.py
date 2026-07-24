from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.contracts import canonical_json, sha256_json

_STAGE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _normalized_state(value: Mapping[str, object]) -> Mapping[str, object]:
    normalized = json.loads(canonical_json(value))
    return _freeze_json(normalized)


@dataclass(frozen=True)
class Checkpoint:
    sequence: int
    stage: str
    identity: RunIdentity
    completed_stages: tuple[str, ...]
    state: Mapping[str, object]
    state_sha256: str
    previous_checkpoint_sha256: str | None
    checkpoint_sha256: str

    def content_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "identity": self.identity.to_dict(),
            "completed_stages": self.completed_stages,
            "state": self.state,
            "state_sha256": self.state_sha256,
            "previous_checkpoint_sha256": self.previous_checkpoint_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.content_dict(), "checkpoint_sha256": self.checkpoint_sha256}


class CheckpointStore:
    def __init__(self, store: RunStore, identity: RunIdentity) -> None:
        if store.run_id != identity.run_id:
            raise ValueError("checkpoint identity does not match the run directory")
        self._store = store
        self._identity = identity

    def save(self, stage: str, state: Mapping[str, object]) -> Checkpoint:
        if not isinstance(stage, str) or not _STAGE.fullmatch(stage):
            raise ValueError("checkpoint stage is invalid")
        previous = self.load_latest(self._identity)
        sequence = previous.sequence + 1 if previous is not None else 1
        completed_stages = (*previous.completed_stages, stage) if previous is not None else (stage,)
        frozen_state = _normalized_state(state)
        state_sha256 = sha256_json(frozen_state)
        checkpoint = Checkpoint(
            sequence=sequence,
            stage=stage,
            identity=self._identity,
            completed_stages=completed_stages,
            state=frozen_state,
            state_sha256=state_sha256,
            previous_checkpoint_sha256=(
                previous.checkpoint_sha256 if previous is not None else None
            ),
            checkpoint_sha256="",
        )
        checkpoint = Checkpoint(
            **{
                **checkpoint.__dict__,
                "checkpoint_sha256": sha256_json(checkpoint.content_dict()),
            }
        )
        self._store.write_json(
            f"checkpoints/{sequence:04d}-{stage}.json",
            checkpoint.to_dict(),
        )
        return checkpoint

    def load_latest(self, expected_identity: RunIdentity) -> Checkpoint | None:
        if expected_identity != self._identity:
            raise ValueError("checkpoint identity does not match expected identity")
        checkpoint_dir = self._store.path_for("checkpoints")
        if not checkpoint_dir.exists():
            return None
        paths = sorted(checkpoint_dir.glob("*.json"))
        if not paths:
            return None

        previous: Checkpoint | None = None
        for expected_sequence, path in enumerate(paths, start=1):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"checkpoint is not valid JSON: {path.name}") from exc
            current = self._parse_checkpoint(value)
            if current.sequence != expected_sequence:
                raise ValueError("checkpoint sequence is not contiguous")
            expected_name = f"{current.sequence:04d}-{current.stage}.json"
            if path.name != expected_name:
                raise ValueError("checkpoint filename does not match its content")
            if current.identity != expected_identity:
                raise ValueError("checkpoint identity does not match expected identity")
            if current.state_sha256 != sha256_json(current.state):
                raise ValueError("checkpoint state hash does not match its state")
            expected_previous_hash = previous.checkpoint_sha256 if previous is not None else None
            if current.previous_checkpoint_sha256 != expected_previous_hash:
                raise ValueError("checkpoint hash chain is broken")
            expected_stages = (
                (*previous.completed_stages, current.stage)
                if previous is not None
                else (current.stage,)
            )
            if current.completed_stages != expected_stages:
                raise ValueError("checkpoint completed stages are invalid")
            if current.checkpoint_sha256 != sha256_json(current.content_dict()):
                raise ValueError("checkpoint hash does not match its content")
            previous = current
        return previous

    @staticmethod
    def _parse_checkpoint(value: object) -> Checkpoint:
        if not isinstance(value, dict):
            raise ValueError("checkpoint must be a JSON object")
        expected_keys = {
            "sequence",
            "stage",
            "identity",
            "completed_stages",
            "state",
            "state_sha256",
            "previous_checkpoint_sha256",
            "checkpoint_sha256",
        }
        if set(value) != expected_keys:
            raise ValueError("checkpoint fields are invalid")
        sequence = value["sequence"]
        stage = value["stage"]
        completed_stages = value["completed_stages"]
        state = value["state"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("checkpoint sequence is invalid")
        if not isinstance(stage, str) or not _STAGE.fullmatch(stage):
            raise ValueError("checkpoint stage is invalid")
        if not isinstance(completed_stages, list) or any(
            not isinstance(item, str) for item in completed_stages
        ):
            raise ValueError("checkpoint completed stages are invalid")
        if not isinstance(state, dict):
            raise ValueError("checkpoint state must be a JSON object")
        if not isinstance(value["state_sha256"], str):
            raise ValueError("checkpoint state hash is invalid")
        if not isinstance(value["checkpoint_sha256"], str):
            raise ValueError("checkpoint hash is invalid")
        previous_hash = value["previous_checkpoint_sha256"]
        if previous_hash is not None and not isinstance(previous_hash, str):
            raise ValueError("checkpoint previous hash is invalid")
        return Checkpoint(
            sequence=sequence,
            stage=stage,
            identity=RunIdentity.from_dict(value["identity"]),
            completed_stages=tuple(completed_stages),
            state=_normalized_state(state),
            state_sha256=value["state_sha256"],
            previous_checkpoint_sha256=previous_hash,
            checkpoint_sha256=value["checkpoint_sha256"],
        )
