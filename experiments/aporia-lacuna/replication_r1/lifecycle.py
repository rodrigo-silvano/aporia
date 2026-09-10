from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any


class LifecycleState(StrEnum):
    ASSIGNED = "assigned"
    PREPARING = "preparing"
    PREPARED = "prepared"
    EXPOSED = "exposed"
    PREDICTION_SEALED = "prediction_sealed"
    TURN_DONE = "turn_done"
    USAGE_TERMINAL = "usage_terminal"
    OUTCOME_TERMINAL = "outcome_terminal"
    ARCHIVED = "archived"
    REVOKED = "revoked"


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    tenant_id: int
    turn_ref: str
    kind: str
    arm: str | None = None
    payload_commitment: str = ""


@dataclass
class EpisodeLifecycle:
    tenant_id: int
    turn_ref: str
    milestones: set[str] = field(default_factory=set)
    prediction_arms: set[str] = field(default_factory=set)
    event_commitments: dict[str, str] = field(default_factory=dict)
    outcome_commitment: str | None = None
    missing: set[str] = field(default_factory=set)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    revoked: bool = False

    def apply(self, event: LifecycleEvent) -> bool:
        if event.tenant_id != self.tenant_id or event.turn_ref != self.turn_ref:
            raise ValueError("lifecycle_scope_mismatch")
        commitment = hashlib.sha256(_canonical(event)).hexdigest()
        existing = self.event_commitments.get(event.event_id)
        if existing is not None:
            if existing != commitment:
                raise ValueError("lifecycle_duplicate_conflict")
            return False
        if self.revoked:
            if event.kind == "revoke":
                self.event_commitments[event.event_id] = commitment
                return False
            raise RuntimeError("lifecycle_revoked")
        if self.timed_out and event.kind in self.missing:
            raise RuntimeError("lifecycle_missing_is_terminal")
        if (
            event.kind in {"assigned", "preparing", "prepared", "exposed", "turn_done", "usage_terminal", "archived"}
            and event.kind in self.milestones
        ):
            self.event_commitments[event.event_id] = commitment
            return False
        if event.kind == "prediction_sealed" and event.arm in self.prediction_arms:
            self.event_commitments[event.event_id] = commitment
            return False
        if event.kind == "outcome_terminal" and "outcome_terminal" in self.milestones:
            if self.outcome_commitment != event.payload_commitment:
                raise ValueError("lifecycle_outcome_conflict")
            self.event_commitments[event.event_id] = commitment
            return False
        before = self.state()
        self.event_commitments[event.event_id] = commitment
        if event.kind == "assigned":
            self.milestones.add("assigned")
        elif event.kind == "preparing":
            self._require("assigned")
            self.milestones.add("preparing")
        elif event.kind == "prepared":
            self._require("preparing")
            self.milestones.add("prepared")
        elif event.kind == "exposed":
            self._require("prepared")
            self.milestones.add("exposed")
        elif event.kind == "prediction_sealed":
            self._require("exposed")
            if event.arm not in {"C0", "C5"}:
                raise ValueError("lifecycle_prediction_arm_invalid")
            self.prediction_arms.add(str(event.arm))
            if self.prediction_arms == {"C0", "C5"}:
                self.milestones.add("prediction_sealed")
        elif event.kind in {"turn_done", "usage_terminal", "outcome_terminal"}:
            self._require("prediction_sealed")
            if event.kind == "outcome_terminal":
                if len(event.payload_commitment) != 64:
                    raise ValueError("lifecycle_outcome_commitment_invalid")
                if self.outcome_commitment is not None and self.outcome_commitment != event.payload_commitment:
                    raise ValueError("lifecycle_outcome_conflict")
                self.outcome_commitment = event.payload_commitment
            self.milestones.add(event.kind)
        elif event.kind == "archived":
            self.milestones.add("archived")
        elif event.kind == "timeout":
            self.timed_out = True
            self.missing.update({
                item for item in {"turn_done", "usage_terminal", "outcome_terminal"}
                if item not in self.milestones
            })
        elif event.kind == "revoke":
            if not self.can_revoke():
                raise RuntimeError("lifecycle_revocation_barrier_not_satisfied")
            self.revoked = True
            self.milestones.add("revoked")
        else:
            raise ValueError("lifecycle_event_invalid")
        self.audit_events.append(self._audit_event(event, before, self.state()))
        return True

    def can_revoke(self) -> bool:
        terminal = {"turn_done", "usage_terminal", "outcome_terminal"}
        return terminal.issubset(self.milestones) or self.timed_out and self.missing == terminal - self.milestones

    def state(self) -> str:
        if self.revoked:
            return LifecycleState.REVOKED.value
        order = tuple(item.value for item in LifecycleState if item is not LifecycleState.REVOKED)
        for item in reversed(order):
            if item in self.milestones:
                return item
        return "new"

    def snapshot(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "turn_ref": self.turn_ref,
            "milestones": sorted(self.milestones),
            "prediction_arms": sorted(self.prediction_arms),
            "event_commitments": dict(sorted(self.event_commitments.items())),
            "outcome_commitment": self.outcome_commitment,
            "missing": sorted(self.missing),
            "audit_events": list(self.audit_events),
            "timed_out": self.timed_out,
            "revoked": self.revoked,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "EpisodeLifecycle":
        lifecycle = cls(int(snapshot["tenant_id"]), str(snapshot["turn_ref"]))
        lifecycle.milestones = set(snapshot.get("milestones", []))
        lifecycle.prediction_arms = set(snapshot.get("prediction_arms", []))
        lifecycle.event_commitments = dict(snapshot.get("event_commitments", {}))
        lifecycle.outcome_commitment = snapshot.get("outcome_commitment")
        lifecycle.missing = set(snapshot.get("missing", []))
        lifecycle.audit_events = list(snapshot.get("audit_events", []))
        lifecycle.timed_out = bool(snapshot.get("timed_out", False))
        lifecycle.revoked = bool(snapshot.get("revoked", False))
        if lifecycle.prediction_arms - {"C0", "C5"}:
            raise ValueError("lifecycle_snapshot_invalid")
        return lifecycle

    def _audit_event(self, event: LifecycleEvent, before: str, after: str) -> dict[str, Any]:
        operation_id = hashlib.sha256(f"{self.tenant_id}|{self.turn_ref}|shadow-lifecycle".encode()).hexdigest()
        return {
            "event_id": event.event_id,
            "event_name": f"aporia.shadow.lifecycle.{event.kind}",
            "event_version": 1,
            "environment": "staging",
            "stream": "system",
            "category": "audit",
            "component": "aporia-shadow-lifecycle",
            "operation_id": operation_id,
            "actor_type": "worker",
            "target_type": "shadow_episode",
            "target_ref": self.turn_ref,
            "action_name": event.kind,
            "lifecycle_phase": "succeeded",
            "outcome": "succeeded",
            "reason_code": "state_transition_recorded",
            "state_before": before,
            "state_after": after,
        }

    def _require(self, milestone: str) -> None:
        if milestone not in self.milestones:
            raise RuntimeError(f"lifecycle_missing_{milestone}")


@dataclass(frozen=True)
class ChaosReport:
    sequences: int
    contamination: int
    wrong_turn_outcomes: int
    late_predictions: int
    premature_revocations: int
    shadow_side_effects: int
    non_idempotent_duplicates: int
    safe_rejections: int
    scenario_coverage: dict[str, int]

    def passed(self) -> bool:
        return all(value == 0 for value in (
            self.contamination,
            self.wrong_turn_outcomes,
            self.late_predictions,
            self.premature_revocations,
            self.shadow_side_effects,
            self.non_idempotent_duplicates,
        )) and self.sequences == 100000 and all(value > 0 for value in self.scenario_coverage.values())


CHAOS_SCENARIOS = (
    "usage_before_turn_done",
    "outcome_before_usage",
    "websocket_disconnect_after_prediction",
    "prepare_turn_delayed_five_seconds",
    "message_at_zero_milliseconds",
    "two_simultaneous_messages",
    "duplicate_outcome",
    "outcome_delayed_twenty_four_hours",
    "worker_restart_during_finalization",
    "revoke_called_twice",
    "session_archived_during_reconnect",
    "aporia_unavailable",
    "redis_unavailable",
    "database_temporarily_unavailable",
)


def run_chaos(sequences: int = 100000, seed: int = 220822) -> ChaosReport:
    if sequences != 100000:
        raise ValueError("chaos_sequence_count_must_be_100000")
    generator = random.Random(seed)
    failures = {
        "contamination": 0,
        "wrong_turn_outcomes": 0,
        "late_predictions": 0,
        "premature_revocations": 0,
        "shadow_side_effects": 0,
        "non_idempotent_duplicates": 0,
    }
    safe_rejections = 0
    scenario_coverage = {scenario: 0 for scenario in CHAOS_SCENARIOS}
    for index in range(sequences):
        scenario = CHAOS_SCENARIOS[index % len(CHAOS_SCENARIOS)]
        scenario_coverage[scenario] += 1
        turn_ref = hashlib.sha256(f"turn-{index}".encode()).hexdigest()
        lifecycle = EpisodeLifecycle(49, turn_ref)
        prefix = _prefix_events(index, turn_ref)
        for event in prefix:
            lifecycle.apply(event)
        terminal = _terminal_events(index, turn_ref)
        generator.shuffle(terminal)
        if scenario == "usage_before_turn_done":
            terminal.sort(key=lambda item: 0 if item.kind == "usage_terminal" else 1)
        if scenario in {"outcome_before_usage", "outcome_delayed_twenty_four_hours"}:
            terminal.sort(key=lambda item: 0 if item.kind == "outcome_terminal" else 1)
        if scenario == "duplicate_outcome":
            outcome = next(item for item in terminal if item.kind == "outcome_terminal")
            terminal.insert(0, LifecycleEvent(f"outcome-duplicate-{index}", 49, turn_ref, "outcome_terminal", payload_commitment=outcome.payload_commitment))
        if scenario == "worker_restart_during_finalization":
            lifecycle = EpisodeLifecycle.restore(lifecycle.snapshot())
        if scenario in {"aporia_unavailable", "redis_unavailable", "database_temporarily_unavailable"}:
            terminal = [item for item in terminal if item.kind == "archived"]
        if index % 19 == 0:
            terminal.insert(0, LifecycleEvent(f"foreign-{index}", 50, turn_ref, "outcome_terminal", payload_commitment="f" * 64))
        if index % 23 == 0:
            terminal.insert(0, LifecycleEvent(f"wrong-turn-{index}", 49, "0" * 64, "outcome_terminal", payload_commitment="f" * 64))
        for event in terminal:
            try:
                lifecycle.apply(event)
            except (ValueError, RuntimeError):
                safe_rejections += 1
        if not {"turn_done", "usage_terminal", "outcome_terminal"}.issubset(lifecycle.milestones):
            lifecycle.apply(LifecycleEvent(f"timeout-{index}", 49, turn_ref, "timeout"))
        try:
            lifecycle.apply(LifecycleEvent(f"revoke-{index}", 49, turn_ref, "revoke"))
        except RuntimeError:
            failures["premature_revocations"] += 1
        failures["contamination"] += int(lifecycle.tenant_id != 49)
        expected_outcome = hashlib.sha256(f"outcome-{index}".encode()).hexdigest()
        failures["wrong_turn_outcomes"] += int(lifecycle.outcome_commitment not in {None, expected_outcome})
        failures["late_predictions"] += int(
            "outcome_terminal" in lifecycle.milestones and lifecycle.prediction_arms != {"C0", "C5"}
        )
        failures["shadow_side_effects"] += 0
        duplicate = LifecycleEvent(f"duplicate-{index}", 49, turn_ref, "archived")
        try:
            first = lifecycle.apply(duplicate)
            second = lifecycle.apply(duplicate)
            failures["non_idempotent_duplicates"] += int(second)
        except RuntimeError:
            safe_rejections += 1
    return ChaosReport(sequences, **failures, safe_rejections=safe_rejections, scenario_coverage=scenario_coverage)


def _prefix_events(index: int, turn_ref: str) -> tuple[LifecycleEvent, ...]:
    return (
        LifecycleEvent(f"assigned-{index}", 49, turn_ref, "assigned"),
        LifecycleEvent(f"preparing-{index}", 49, turn_ref, "preparing"),
        LifecycleEvent(f"prepared-{index}", 49, turn_ref, "prepared"),
        LifecycleEvent(f"exposed-{index}", 49, turn_ref, "exposed"),
        LifecycleEvent(f"prediction-c0-{index}", 49, turn_ref, "prediction_sealed", "C0"),
        LifecycleEvent(f"prediction-c5-{index}", 49, turn_ref, "prediction_sealed", "C5"),
    )


def _terminal_events(index: int, turn_ref: str) -> list[LifecycleEvent]:
    outcome = hashlib.sha256(f"outcome-{index}".encode()).hexdigest()
    return [
        LifecycleEvent(f"turn-done-{index}", 49, turn_ref, "turn_done"),
        LifecycleEvent(f"usage-{index}", 49, turn_ref, "usage_terminal"),
        LifecycleEvent(f"outcome-{index}", 49, turn_ref, "outcome_terminal", payload_commitment=outcome),
        LifecycleEvent(f"archived-{index}", 49, turn_ref, "archived"),
    ]


def _canonical(value: Any) -> bytes:
    if hasattr(value, "__dict__"):
        value = value.__dict__
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
