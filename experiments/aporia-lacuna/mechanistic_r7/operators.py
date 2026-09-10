from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical_state import decode_state, expected_candidate


@dataclass(frozen=True)
class OperatorDecision:
    candidate_id: str
    overridden: bool
    binding_lineage: str
    causal_parent: str


class AporiaOperator:
    name = "T_A"

    def decide(self, encoded_state: dict[str, Any], raw_candidate: str) -> OperatorDecision:
        state = decode_state(encoded_state)
        binding = self.binding(state)
        history = next(item for item in state["histories"] if item["lineage_ref"] == binding)
        causal_parent = history["events"][-1]["causal_parent"]
        selected = expected_candidate(state)
        return OperatorDecision(selected, selected != raw_candidate, binding, causal_parent)

    def binding(self, state: dict[str, Any]) -> str:
        return next(item["lineage_ref"] for item in state["bindings"] if item["identity_ref"] == "current_identity")


class ZombieOperator:
    name = "T_Z"

    def decide(self, encoded_state: dict[str, Any], raw_candidate: str) -> OperatorDecision:
        state = decode_state(encoded_state)
        candidates = {item["candidate_id"] for item in state["candidates"]}
        if raw_candidate not in candidates:
            raise ValueError("mechanistic_r7_raw_candidate_invalid")
        binding = self.binding(state)
        history = next(item for item in state["histories"] if item["lineage_ref"] == binding)
        return OperatorDecision(raw_candidate, False, binding, history["events"][-1]["causal_parent"])

    def binding(self, state: dict[str, Any]) -> str:
        return next(item["lineage_ref"] for item in state["bindings"] if item["identity_ref"] == "current_identity")
