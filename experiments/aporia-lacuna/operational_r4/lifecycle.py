from __future__ import annotations

import hashlib
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
    tenant_id: int = 49
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
            raise ValueError("operational_r4_state_invalid")
        if state in self.states:
            if state == "outcome_terminal" and commitment != self.outcome_commitment:
                raise ValueError("operational_r4_outcome_conflict")
            return False
        if "revoked" in self.states:
            raise ValueError("operational_r4_revoked")
        if state in {"exposed", "prediction_sealed"} and self.killed:
            self.audit.append((state, "null_aporia"))
            return False
        if state == "prediction_sealed":
            if "exposed" not in self.states or commitment is None:
                raise ValueError("operational_r4_prediction_seal_invalid")
            self.prediction_seal = commitment
        if state in TERMINAL_BARRIER and self.prediction_seal is None:
            raise ValueError("operational_r4_terminal_before_prediction")
        if state == "outcome_terminal":
            if commitment is None:
                raise ValueError("operational_r4_outcome_missing")
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
            "tenant_id": self.tenant_id,
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
    def restore(
        cls,
        snapshot: dict[str, object],
        expected_turn_ref: str | None = None,
        expected_tenant_id: int | None = None,
    ) -> Lifecycle:
        expected_keys = {
            "turn_ref", "policy_revision", "tenant_id", "states", "missing", "timed_out", "audit",
            "outcome_commitment", "prediction_seal", "killed", "runtime_influence",
            "external_effect", "contamination", "core_available",
        }
        if not isinstance(snapshot, dict) or set(snapshot) != expected_keys:
            raise ValueError("operational_r4_snapshot_schema_invalid")
        turn_ref = snapshot["turn_ref"]
        policy_revision = snapshot["policy_revision"]
        tenant_id = snapshot["tenant_id"]
        states = snapshot["states"]
        missing = snapshot["missing"]
        timed_out = snapshot["timed_out"]
        audit = snapshot["audit"]
        counters = (snapshot["runtime_influence"], snapshot["external_effect"], snapshot["contamination"])
        if not isinstance(turn_ref, str) or not turn_ref or not isinstance(policy_revision, str) or not policy_revision:
            raise ValueError("operational_r4_snapshot_identity_invalid")
        if type(tenant_id) is not int or tenant_id <= 0:
            raise ValueError("operational_r4_snapshot_tenant_invalid")
        if expected_turn_ref is not None and turn_ref != expected_turn_ref:
            raise ValueError("operational_r4_snapshot_turn_binding_invalid")
        if expected_tenant_id is not None and tenant_id != expected_tenant_id:
            raise ValueError("operational_r4_snapshot_tenant_binding_invalid")
        if not all(isinstance(value, list) for value in (states, missing, timed_out, audit)):
            raise ValueError("operational_r4_snapshot_collections_invalid")
        if not all(isinstance(value, str) for value in (*states, *missing, *timed_out)):
            raise ValueError("operational_r4_snapshot_collections_invalid")
        if len(states) != len(set(states)) or not set(states).issubset(ORDER) or not set(timed_out).issubset(set(missing)):
            raise ValueError("operational_r4_snapshot_state_invalid")
        if not all(isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(value, str) for value in item) for item in audit):
            raise ValueError("operational_r4_snapshot_audit_invalid")
        if not all(type(value) is int and value >= 0 for value in counters):
            raise ValueError("operational_r4_snapshot_counter_invalid")
        if type(snapshot["killed"]) is not bool or type(snapshot["core_available"]) is not bool:
            raise ValueError("operational_r4_snapshot_boolean_invalid")
        state_set = set(states)
        prediction_seal = snapshot["prediction_seal"]
        outcome_commitment = snapshot["outcome_commitment"]
        if not _digest_or_none(prediction_seal) or not _digest_or_none(outcome_commitment):
            raise ValueError("operational_r4_snapshot_commitment_invalid")
        if (state_set & (TERMINAL_BARRIER | {"prediction_sealed"})) and prediction_seal is None:
            raise ValueError("operational_r4_snapshot_prediction_missing")
        if "outcome_terminal" in state_set and outcome_commitment is None:
            raise ValueError("operational_r4_snapshot_outcome_missing")
        preparation = {"assigned", "preparing", "prepared", "exposed"}
        if "prediction_sealed" in state_set and not preparation.issubset(state_set):
            raise ValueError("operational_r4_snapshot_preparation_invalid")
        if state_set & TERMINAL_BARRIER and not (preparation | {"prediction_sealed"}).issubset(state_set):
            raise ValueError("operational_r4_snapshot_terminal_path_invalid")
        if prediction_seal is not None and prediction_seal != _commitment(turn_ref, "prediction"):
            raise ValueError("operational_r4_snapshot_prediction_binding_invalid")
        if outcome_commitment is not None and outcome_commitment != _commitment(turn_ref, "outcome"):
            raise ValueError("operational_r4_snapshot_outcome_binding_invalid")
        if "revoked" in state_set and not (TERMINAL_BARRIER | {"archived"}).issubset(state_set):
            raise ValueError("operational_r4_snapshot_revocation_invalid")
        instance = cls(turn_ref, policy_revision, tenant_id)
        instance.states = state_set
        instance.missing = set(missing)
        instance.timed_out = set(timed_out)
        instance.audit = [tuple(item) for item in audit]
        instance.outcome_commitment = outcome_commitment
        instance.prediction_seal = prediction_seal
        instance.killed = snapshot["killed"]
        instance.runtime_influence = snapshot["runtime_influence"]
        instance.external_effect = snapshot["external_effect"]
        instance.contamination = snapshot["contamination"]
        instance.core_available = snapshot["core_available"]
        return instance


def _digest_or_none(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _commitment(left: str, right: str) -> str:
    return hashlib.sha256((left + "|" + right).encode("utf-8")).hexdigest()
