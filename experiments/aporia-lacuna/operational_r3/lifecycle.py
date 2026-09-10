from __future__ import annotations

from dataclasses import dataclass, field


ORDER = (
    "assigned",
    "preparing",
    "prepared",
    "exposed",
    "prediction_sealed",
    "turn_done",
    "usage_terminal",
    "outcome_terminal",
    "archived",
    "revoked",
)
TERMINAL_BARRIER = {"turn_done", "usage_terminal", "outcome_terminal"}


@dataclass
class Lifecycle:
    turn_ref: str
    policy_revision: str = "policy-v1"
    states: set[str] = field(default_factory=set)
    missing: set[str] = field(default_factory=set)
    timed_out: set[str] = field(default_factory=set)
    audit: list[tuple[str, str]] = field(default_factory=list)
    outcome_commitment: str | None = None
    prediction_seal: str | None = None
    killed: bool = False
    runtime_influence: int = 0
    external_effect: int = 0
    contamination: int = 0
    core_available: bool = True

    def transition(self, state: str, commitment: str | None = None) -> bool:
        if state not in ORDER:
            raise ValueError("operational_r3_state_invalid")
        if state in self.states:
            if state == "outcome_terminal" and commitment != self.outcome_commitment:
                raise ValueError("operational_r3_outcome_conflict")
            return False
        if "revoked" in self.states:
            raise ValueError("operational_r3_revoked")
        if state in {"exposed", "prediction_sealed"} and self.killed:
            self.audit.append((state, "null_aporia"))
            return False
        if state == "prediction_sealed":
            if "exposed" not in self.states or commitment is None:
                raise ValueError("operational_r3_prediction_seal_invalid")
            self.prediction_seal = commitment
        if state in TERMINAL_BARRIER and self.prediction_seal is None:
            raise ValueError("operational_r3_terminal_before_prediction")
        if state == "outcome_terminal":
            if commitment is None:
                raise ValueError("operational_r3_outcome_missing")
            self.outcome_commitment = commitment
        if state == "revoked" and not self.can_revoke():
            self.audit.append((state, "terminal_barrier_pending"))
            return False
        self.states.add(state)
        self.audit.append((state, "recorded"))
        return True

    def kill(self) -> None:
        self.killed = True
        self.audit.append(("kill_switch", "null_aporia"))

    def timeout(self, components: set[str]) -> None:
        pending = components - self.states
        self.missing.update(pending)
        self.timed_out.update(pending)
        for component in sorted(pending):
            self.audit.append((component, "timed_out"))

    def can_revoke(self) -> bool:
        return TERMINAL_BARRIER.issubset(self.states) and "archived" in self.states

    def snapshot(self) -> dict[str, object]:
        return {
            "turn_ref": self.turn_ref,
            "policy_revision": self.policy_revision,
            "states": sorted(self.states),
            "missing": sorted(self.missing),
            "timed_out": sorted(self.timed_out),
            "audit": list(self.audit),
            "outcome_commitment": self.outcome_commitment,
            "prediction_seal": self.prediction_seal,
            "killed": self.killed,
            "runtime_influence": self.runtime_influence,
            "external_effect": self.external_effect,
            "contamination": self.contamination,
            "core_available": self.core_available,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, object]) -> Lifecycle:
        instance = cls(str(snapshot["turn_ref"]), str(snapshot["policy_revision"]))
        instance.states = set(snapshot["states"])
        instance.missing = set(snapshot["missing"])
        instance.timed_out = set(snapshot["timed_out"])
        instance.audit = [tuple(item) for item in snapshot["audit"]]
        instance.outcome_commitment = snapshot["outcome_commitment"]
        instance.prediction_seal = snapshot["prediction_seal"]
        instance.killed = bool(snapshot["killed"])
        instance.runtime_influence = int(snapshot["runtime_influence"])
        instance.external_effect = int(snapshot["external_effect"])
        instance.contamination = int(snapshot["contamination"])
        instance.core_available = bool(snapshot["core_available"])
        return instance
