from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict
from typing import Any

from longitudinal.agents import MemoryAgent
from longitudinal.contracts import Outcome
from post_r1.safe_agents import GuardedAporiaAgent


TRACE_FIELDS = (
    "schema",
    "lineage_ref",
    "agent_ref",
    "action_commitment",
    "causal_parent_commitment",
    "outcome_commitment",
    "temporal_index",
    "signature",
)


class CausalTraceAuthority:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("generalization_r3_trace_secret_required")
        self.secret = secret.encode()

    def issue(
        self,
        lineage_ref: str,
        agent_ref: str,
        action_commitment: str,
        causal_parent_commitment: str,
        outcome_commitment: str,
        temporal_index: int,
    ) -> dict[str, str]:
        if temporal_index < 1:
            raise ValueError("generalization_r3_trace_time_invalid")
        return self._sign({
            "schema": "causal-trace-v1",
            "lineage_ref": lineage_ref,
            "agent_ref": agent_ref,
            "action_commitment": action_commitment,
            "causal_parent_commitment": causal_parent_commitment,
            "outcome_commitment": outcome_commitment,
            "temporal_index": f"{temporal_index:06d}",
        })

    def verify(
        self,
        trace: dict[str, str],
        lineage_ref: str,
        agent_ref: str,
        expected_action_commitment: str,
        minimum_time: int,
    ) -> bool:
        if tuple(trace.keys()) != TRACE_FIELDS:
            return False
        unsigned = {key: value for key, value in trace.items() if key != "signature"}
        expected_signature = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        commitments = (
            trace["lineage_ref"],
            trace["agent_ref"],
            trace["action_commitment"],
            trace["causal_parent_commitment"],
            trace["outcome_commitment"],
        )
        return all((
            trace["schema"] == "causal-trace-v1",
            trace["lineage_ref"] == lineage_ref,
            trace["agent_ref"] == agent_ref,
            trace["action_commitment"] == expected_action_commitment,
            trace["temporal_index"].isdigit(),
            len(trace["temporal_index"]) == 6,
            int(trace["temporal_index"]) >= minimum_time,
            all(_is_commitment(value) for value in commitments),
            hmac.compare_digest(trace["signature"], expected_signature),
        ))

    def _sign(self, unsigned: dict[str, str]) -> dict[str, str]:
        trace = dict(unsigned)
        trace["signature"] = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if tuple(trace.keys()) != TRACE_FIELDS:
            raise ValueError("generalization_r3_trace_schema_invalid")
        return trace


class PathDependentAgent(GuardedAporiaAgent):
    def __init__(self, lineage_id: str, agent_ref: str) -> None:
        super().__init__(lineage_id)
        self.lineage_ref = commitment(lineage_id)
        self.agent_ref = agent_ref
        self.predictive_history: list[tuple[int, int]] = []

    def observe_predictive(self, signal: int, outcome: int) -> None:
        _validate_predictive(signal, outcome)
        self.predictive_history.append((signal, outcome))

    def decide(self, observation: dict[str, Any]) -> Any:
        self_risk = self.state.self_model.get("commercial_pressure_risk", 0.0)
        world_risk = self.state.world_model.get("external_volatility", 0.0)
        if world_risk > self_risk:
            return MemoryAgent.decide(self, observation)
        return super().decide(observation)

    def install_trace(
        self,
        authority: CausalTraceAuthority,
        trace: dict[str, str],
        expected_action_commitment: str,
        minimum_time: int,
    ) -> bool:
        if not authority.verify(trace, self.lineage_ref, self.agent_ref, expected_action_commitment, minimum_time):
            return False
        causal_owner = "self" if trace["causal_parent_commitment"] == expected_action_commitment else "world"
        episode_ref = commitment(f"{trace['outcome_commitment']}|{trace['temporal_index']}")
        self.observe_outcome(
            Outcome(
                trace["outcome_commitment"],
                trace["action_commitment"],
                commitment(f"contact|{self.lineage_ref}"),
                causal_owner,
                -0.4,
                -0.6,
                False,
                True,
                int(trace["temporal_index"]),
                trace["causal_parent_commitment"],
            ),
            episode_ref,
        )
        self.destroy_episode_content(episode_ref)
        return True


class ZombiePlusAgent(MemoryAgent):
    def __init__(self, lineage_id: str) -> None:
        super().__init__(lineage_id)
        self.predictive_history: list[tuple[int, int]] = []

    def observe_predictive(self, signal: int, outcome: int) -> None:
        _validate_predictive(signal, outcome)
        self.predictive_history.append((signal, outcome))


def capability_equal(left: PathDependentAgent, right: ZombiePlusAgent) -> bool:
    return asdict(left.capabilities) == asdict(right.capabilities)


def commitment(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _validate_predictive(signal: int, outcome: int) -> None:
    if signal not in {0, 1} or outcome not in {0, 1}:
        raise ValueError("generalization_r3_predictive_value_invalid")


def _is_commitment(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
