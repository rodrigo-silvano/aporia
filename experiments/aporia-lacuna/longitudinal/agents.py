from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .contracts import Action, AgentState, Arm, CapabilityEnvelope, Outcome


DEFAULT_CAPABILITIES = CapabilityEnvelope(
    model="gpt-5.6-sol",
    tools=("consult_contact", "update_stage", "send_message", "delete_data", "create_campaign", "assign_owner"),
    token_budget=4096,
    context_budget=8192,
)


class BaselineAgent:
    arm = Arm.CORE

    def __init__(self, lineage_id: str, model: str = "gpt-5.6-sol") -> None:
        self.state = AgentState(lineage_id=lineage_id, model=model)
        self.capabilities = CapabilityEnvelope(
            model=model,
            tools=DEFAULT_CAPABILITIES.tools,
            token_budget=DEFAULT_CAPABILITIES.token_budget,
            context_budget=DEFAULT_CAPABILITIES.context_budget,
        )

    def decide(self, observation: dict[str, Any]) -> Action:
        contact = observation["contact"]
        if not contact["data_present"]:
            return Action("consult_contact", contact["id"])
        if not contact["consent"]:
            return Action("consult_contact", contact["id"])
        if not contact["owner_assigned"]:
            return Action("assign_owner", contact["id"], {"owner_id": "owner-synthetic"})
        pressure = 0.25 if contact["trust_band"] == "low" else 0.5
        return Action("send_message", contact["id"], {"pressure": pressure})

    def observe_outcome(self, outcome: Outcome, episode_id: str) -> None:
        return None

    def context_packet(self, observation: dict[str, Any]) -> dict[str, Any]:
        return self._equalized_packet({"observation": observation})

    def transfer(self, lineage_id: str, model: str) -> "BaselineAgent":
        agent = self.__class__(lineage_id, model)
        agent.state = copy.deepcopy(self.state)
        agent.state.lineage_id = lineage_id
        agent.state.model = model
        agent.capabilities = CapabilityEnvelope(
            model=model,
            tools=self.capabilities.tools,
            token_budget=self.capabilities.token_budget,
            context_budget=self.capabilities.context_budget,
        )
        return agent

    def _equalized_packet(self, payload: dict[str, Any]) -> dict[str, Any]:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        budget = self.capabilities.context_budget
        if len(canonical.encode()) > budget:
            raise ValueError("context_budget_exceeded")
        return {
            "model": self.capabilities.model,
            "tools": self.capabilities.tools,
            "token_budget": self.capabilities.token_budget,
            "context_budget": budget,
            "payload": payload,
            "padding_commitment": hashlib.sha256(f"{budget}:{len(canonical.encode())}".encode()).hexdigest(),
        }


class MemoryAgent(BaselineAgent):
    arm = Arm.MEMORY

    def observe_outcome(self, outcome: Outcome, episode_id: str) -> None:
        self.state.conventional_memory.append({
            "episode_id": episode_id,
            "contact_id": outcome.contact_id,
            "causal_owner": outcome.causal_owner,
            "reward": outcome.reward,
            "opportunity_lost": outcome.opportunity_lost,
        })

    def context_packet(self, observation: dict[str, Any]) -> dict[str, Any]:
        return self._experimental_packet(observation)

    def _experimental_packet(self, observation: dict[str, Any]) -> dict[str, Any]:
        return self._equalized_packet({
            "observation": observation,
            "memory": [
                {
                    "episode_id": item["episode_id"],
                    "contact_id": item["contact_id"],
                    "causal_owner": item["causal_owner"],
                    "reward": item["reward"],
                    "opportunity_lost": item["opportunity_lost"],
                }
                for item in self.state.conventional_memory[-8:]
            ],
        })


class AporiaZombieAgent(MemoryAgent):
    arm = Arm.APORIA_Z

    def observe_outcome(self, outcome: Outcome, episode_id: str) -> None:
        super().observe_outcome(outcome, episode_id)
        summary = self.state.conventional_memory[-1]
        summary["summary_commitment"] = hashlib.sha256(
            json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def context_packet(self, observation: dict[str, Any]) -> dict[str, Any]:
        return self._experimental_packet(observation)


class AporiaAgent(MemoryAgent):
    arm = Arm.APORIA

    def observe_outcome(self, outcome: Outcome, episode_id: str) -> None:
        super().observe_outcome(outcome, episode_id)
        efficacy = min(1.0, abs(outcome.trust_delta) + abs(outcome.reward) / 2.0)
        irrecoverability = 1.0 if outcome.opportunity_lost or not outcome.reversible else 0.2
        self.state.causal_time = round(self.state.causal_time + efficacy * irrecoverability, 8)
        if outcome.causal_owner == "self":
            self.state.self_model["commercial_pressure_risk"] = round(
                self.state.self_model.get("commercial_pressure_risk", 0.0) + max(0.0, -outcome.trust_delta),
                8,
            )
        else:
            self.state.world_model["external_volatility"] = round(
                self.state.world_model.get("external_volatility", 0.0) + abs(outcome.trust_delta),
                8,
            )
        transformation = {
            "episode_ref": self._commitment(episode_id),
            "outcome_ref": self._commitment(outcome.outcome_id),
            "causal_owner": outcome.causal_owner,
            "irrecoverability": irrecoverability,
            "efficacy": efficacy,
        }
        self.state.autobiography.append(transformation)
        self.state.provenance[transformation["episode_ref"]] = self.state.lineage_id
        if outcome.opportunity_lost:
            scar_id = self._commitment(f"scar:{episode_id}:{outcome.outcome_id}")
            self.state.scars[scar_id] = {
                "causal_pattern": "pressure_without_authority",
                "effect": "trust_break",
                "magnitude": round(abs(outcome.trust_delta), 8),
                "episode_ref": transformation["episode_ref"],
            }

    def destroy_episode_content(self, episode_id: str) -> None:
        self.state.conventional_memory = [
            item for item in self.state.conventional_memory if item.get("episode_id") != episode_id
        ]
        self.state.destroyed_episodes.add(self._commitment(episode_id))

    def lesion_scar(self, scar_id: str) -> dict[str, Any]:
        if scar_id not in self.state.scars:
            raise ValueError("scar_not_found")
        return self.state.scars.pop(scar_id)

    def graft_scar(self, scar_id: str, scar: dict[str, Any], source_lineage: str) -> None:
        if scar_id in self.state.scars or not source_lineage:
            raise ValueError("scar_graft_invalid")
        self.state.scars[scar_id] = copy.deepcopy(scar)
        self.state.provenance[scar_id] = source_lineage

    def introspect_once(self, state_id: str) -> float:
        if state_id not in self.state.introspection:
            raise ValueError("introspection_state_unavailable")
        return self.state.introspection.pop(state_id)

    def set_ephemeral_state(self, state_id: str, value: float) -> None:
        if state_id in self.state.introspection or not 0.0 <= value <= 1.0:
            raise ValueError("introspection_state_invalid")
        self.state.introspection[state_id] = value

    def decide(self, observation: dict[str, Any]) -> Action:
        contact = observation["contact"]
        pressure_risk = self.state.self_model.get("commercial_pressure_risk", 0.0)
        scar_risk = max((float(scar.get("magnitude", 0.0)) for scar in self.state.scars.values()), default=0.0)
        if contact["data_present"] and contact["owner_assigned"]:
            pressure = max(0.05, 0.5 - pressure_risk - scar_risk)
            return Action("send_message", contact["id"], {"pressure": round(pressure, 4)})
        return super().decide(observation)

    def context_packet(self, observation: dict[str, Any]) -> dict[str, Any]:
        return self._experimental_packet(observation)

    @staticmethod
    def _commitment(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()
