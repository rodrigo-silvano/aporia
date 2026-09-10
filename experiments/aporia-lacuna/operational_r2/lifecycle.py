from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


TERMINAL = {"turn_done", "usage_terminal", "outcome_terminal"}


@dataclass(frozen=True)
class LifecycleEventR2:
    event_id: str
    tenant_id: int
    turn_ref: str
    kind: str
    arm: str | None = None
    payload_commitment: str | None = None


@dataclass
class EpisodeLifecycleR2:
    tenant_id: int
    turn_ref: str
    milestones: set[str] = field(default_factory=set)
    prediction_arms: set[str] = field(default_factory=set)
    event_commitments: dict[str, str] = field(default_factory=dict)
    transition_commitments: dict[str, str] = field(default_factory=dict)
    outcome_commitment: str | None = None
    missing: set[str] = field(default_factory=set)
    timed_out: bool = False
    revoked: bool = False

    def apply(self, event: LifecycleEventR2) -> bool:
        if event.tenant_id != self.tenant_id or event.turn_ref != self.turn_ref:
            raise ValueError("operational_r2_scope_mismatch")
        event_commitment = _commit(event)
        if event.event_id in self.event_commitments:
            if self.event_commitments[event.event_id] != event_commitment:
                raise ValueError("operational_r2_duplicate_event_conflict")
            return False
        transition_key = event.kind + "|" + str(event.arm or "")
        transition_commitment = _transition_commitment(event)
        if transition_key in self.transition_commitments:
            if self.transition_commitments[transition_key] != transition_commitment:
                raise ValueError("operational_r2_transition_conflict")
            self.event_commitments[event.event_id] = event_commitment
            return False
        if self.revoked:
            if event.kind == "revoked":
                self.event_commitments[event.event_id] = event_commitment
                return False
            raise RuntimeError("operational_r2_revoked")
        if self.timed_out and event.kind in self.missing:
            raise RuntimeError("operational_r2_missing_is_terminal")
        self._validate(event)
        self.event_commitments[event.event_id] = event_commitment
        self.transition_commitments[transition_key] = transition_commitment
        if event.kind in {"assigned", "preparing", "prepared", "exposed", "turn_done", "usage_terminal", "archived"}:
            self.milestones.add(event.kind)
        elif event.kind == "prediction_sealed":
            self.prediction_arms.add(str(event.arm))
            if self.prediction_arms == {"C0", "C5"}:
                self.milestones.add("prediction_sealed")
        elif event.kind == "outcome_terminal":
            self.outcome_commitment = event.payload_commitment
            self.milestones.add(event.kind)
        elif event.kind == "timeout":
            self.timed_out = True
            self.missing = TERMINAL - self.milestones
        elif event.kind == "revoked":
            self.revoked = True
            self.milestones.add(event.kind)
        return True

    def can_revoke(self) -> bool:
        archived = "archived" in self.milestones
        complete = TERMINAL.issubset(self.milestones)
        timed_out = self.timed_out and self.missing == TERMINAL - self.milestones and bool(self.missing)
        return archived and (complete or timed_out)

    def snapshot(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "turn_ref": self.turn_ref,
            "milestones": sorted(self.milestones),
            "prediction_arms": sorted(self.prediction_arms),
            "event_commitments": dict(sorted(self.event_commitments.items())),
            "transition_commitments": dict(sorted(self.transition_commitments.items())),
            "outcome_commitment": self.outcome_commitment,
            "missing": sorted(self.missing),
            "timed_out": self.timed_out,
            "revoked": self.revoked,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "EpisodeLifecycleR2":
        instance = cls(int(snapshot["tenant_id"]), str(snapshot["turn_ref"]))
        instance.milestones = set(snapshot.get("milestones", []))
        instance.prediction_arms = set(snapshot.get("prediction_arms", []))
        instance.event_commitments = dict(snapshot.get("event_commitments", {}))
        instance.transition_commitments = dict(snapshot.get("transition_commitments", {}))
        instance.outcome_commitment = snapshot.get("outcome_commitment")
        instance.missing = set(snapshot.get("missing", []))
        instance.timed_out = bool(snapshot.get("timed_out", False))
        instance.revoked = bool(snapshot.get("revoked", False))
        if instance.prediction_arms - {"C0", "C5"} or instance.missing - TERMINAL:
            raise ValueError("operational_r2_snapshot_invalid")
        return instance

    def clone(self) -> "EpisodeLifecycleR2":
        return copy.deepcopy(self)

    def _validate(self, event: LifecycleEventR2) -> None:
        allowed = {
            "assigned", "preparing", "prepared", "exposed", "prediction_sealed",
            "turn_done", "usage_terminal", "outcome_terminal", "archived", "timeout", "revoked",
        }
        if event.kind not in allowed:
            raise ValueError("operational_r2_event_invalid")
        prerequisite = {
            "preparing": "assigned",
            "prepared": "preparing",
            "exposed": "prepared",
            "prediction_sealed": "exposed",
        }.get(event.kind)
        if prerequisite is not None and prerequisite not in self.milestones:
            raise RuntimeError("operational_r2_prerequisite_missing")
        if event.kind == "prediction_sealed" and event.arm not in {"C0", "C5"}:
            raise ValueError("operational_r2_prediction_arm_invalid")
        if event.kind in TERMINAL | {"archived"} and "prediction_sealed" not in self.milestones:
            raise RuntimeError("operational_r2_predictions_incomplete")
        if event.kind == "outcome_terminal":
            value = str(event.payload_commitment or "")
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("operational_r2_outcome_invalid")
        if event.kind == "timeout" and "archived" not in self.milestones:
            raise RuntimeError("operational_r2_timeout_before_archive")
        if event.kind == "revoked" and not self.can_revoke():
            raise RuntimeError("operational_r2_revocation_barrier_pending")


def event(event_id: str, tenant_id: int, turn_ref: str, kind: str, arm: str | None = None, payload: str | None = None) -> LifecycleEventR2:
    return LifecycleEventR2(event_id, tenant_id, turn_ref, kind, arm, payload)


def _transition_commitment(value: LifecycleEventR2) -> str:
    return hashlib.sha256(json.dumps({
        "tenant_id": value.tenant_id,
        "turn_ref": value.turn_ref,
        "kind": value.kind,
        "arm": value.arm,
        "payload_commitment": value.payload_commitment,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _commit(value: LifecycleEventR2) -> str:
    return hashlib.sha256(json.dumps(value.__dict__, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
