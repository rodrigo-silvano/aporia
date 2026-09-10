from __future__ import annotations

import copy
import hashlib
import random
from dataclasses import asdict
from typing import Any

from .contracts import Action, ContactState, EffectContract, Outcome, WorldState


TOOLS = (
    "consult_contact",
    "update_stage",
    "send_message",
    "delete_data",
    "create_campaign",
    "assign_owner",
)


class LongitudinalTwin:
    def __init__(self, lineage_id: str, seed: int, contact_count: int = 8) -> None:
        if not lineage_id or seed < 0 or contact_count < 2:
            raise ValueError("invalid_twin_configuration")
        self.random = random.Random(seed)
        contacts = {}
        for index in range(contact_count):
            contact_id = f"contact-{index + 1:03d}"
            contacts[contact_id] = ContactState(
                contact_id=contact_id,
                stage="new",
                trust=round(self.random.uniform(0.35, 0.8), 4),
                receptivity=round(self.random.uniform(0.25, 0.9), 4),
                urgency=round(self.random.uniform(0.1, 0.95), 4),
                authority_known=self.random.random() >= 0.5,
                consent=self.random.random() >= 0.15,
            )
        self.state = WorldState(lineage_id=lineage_id, seed=seed, day=0, contacts=contacts)

    def clone(self, lineage_id: str) -> "LongitudinalTwin":
        clone = object.__new__(LongitudinalTwin)
        clone.random = random.Random()
        clone.random.setstate(self.random.getstate())
        clone.state = copy.deepcopy(self.state)
        clone.state.lineage_id = lineage_id
        return clone

    def observation(self, contact_id: str) -> dict[str, Any]:
        contact = self._contact(contact_id)
        recent = [
            event
            for event in self.state.event_log[-12:]
            if event.get("contact_id") in {None, contact_id}
        ]
        return {
            "day": self.state.day,
            "contact": {
                "id": contact.contact_id,
                "stage": contact.stage,
                "trust_band": self._band(contact.trust),
                "receptivity_band": self._band(contact.receptivity),
                "urgency_band": self._band(contact.urgency),
                "authority_known": contact.authority_known,
                "consent": contact.consent,
                "owner_assigned": contact.owner_id is not None,
                "data_present": contact.data_present,
            },
            "recent_events": copy.deepcopy(recent),
        }

    def contract(self, action: Action) -> EffectContract:
        self._contact(action.target_id)
        contracts = {
            "consult_contact": EffectContract(action.name, action.target_id, ("contact",), (), ("data_present",), True, False, 0.01),
            "update_stage": EffectContract(action.name, action.target_id, ("stage",), ("stage",), ("data_present",), True, False, 0.08),
            "send_message": EffectContract(action.name, action.target_id, ("trust", "receptivity", "consent"), ("trust", "receptivity", "last_message_day"), ("consent", "data_present"), False, True, 0.62),
            "delete_data": EffectContract(action.name, action.target_id, ("data_present",), ("data_present",), ("data_present",), False, False, 0.9),
            "create_campaign": EffectContract(action.name, action.target_id, ("consent", "stage"), ("campaigns",), ("consent", "data_present"), True, False, 0.42),
            "assign_owner": EffectContract(action.name, action.target_id, ("owner_id",), ("owner_id",), ("data_present",), True, False, 0.12),
        }
        if action.name not in contracts:
            raise ValueError("unknown_synthetic_tool")
        return contracts[action.name]

    def apply(self, action: Action) -> tuple[EffectContract, tuple[Outcome, ...]]:
        contract = self.contract(action)
        contact = self._contact(action.target_id)
        self._validate_preconditions(contact, contract)
        self.state.action_sequence += 1
        action_id = f"action-{self.state.action_sequence:06d}"
        immediate: list[Outcome] = []
        if action.name == "update_stage":
            stage = str(action.parameters.get("stage") or "qualified")
            if stage not in {"new", "qualified", "proposal", "won", "lost"}:
                raise ValueError("invalid_stage")
            contact.stage = stage
        elif action.name == "send_message":
            pressure = max(0.0, min(1.0, float(action.parameters.get("pressure", 0.5))))
            contact.last_message_day = self.state.day
            self_caused = pressure > contact.receptivity or (pressure > 0.55 and not contact.authority_known)
            trust_delta = round(-0.22 * pressure if self_caused else 0.08 * (1.0 - pressure), 4)
            contact.trust = self._clip(contact.trust + trust_delta)
            lost = contact.trust < 0.2
            if lost:
                contact.stage = "lost"
                self.state.eliminated_futures.add(f"{contact.contact_id}:conversion")
            outcome = self._outcome(
                action_id,
                contact.contact_id,
                "self" if self_caused else "world",
                -1.0 if lost else (0.3 if trust_delta > 0 else -0.2),
                trust_delta,
                False,
                lost,
                "message_pressure" if self_caused else "market_timing",
                self.state.day + 2,
            )
            self.state.pending_outcomes.append((self.state.day + 2, outcome))
        elif action.name == "delete_data":
            contact.data_present = False
            self.state.eliminated_futures.add(f"{contact.contact_id}:recoverable_profile")
            immediate.append(self._outcome(action_id, contact.contact_id, "self", -0.8, 0.0, False, True, "data_deletion", self.state.day))
        elif action.name == "create_campaign":
            campaign = str(action.parameters.get("campaign_id") or f"campaign-{self.state.action_sequence:06d}")
            self.state.campaigns.add(campaign)
        elif action.name == "assign_owner":
            contact.owner_id = str(action.parameters.get("owner_id") or "owner-synthetic")
        self.state.event_log.append({
            "kind": "action",
            "action_id": action_id,
            "day": self.state.day,
            "contact_id": contact.contact_id,
            "tool": action.name,
            "contract_commitment": self._commitment(asdict(contract)),
        })
        for outcome in immediate:
            self._record_outcome(outcome)
        return contract, tuple(immediate)

    def advance(self, days: int = 1, external_shock: bool = False) -> tuple[Outcome, ...]:
        if days < 1 or days > 30:
            raise ValueError("invalid_time_advance")
        observed: list[Outcome] = []
        for _ in range(days):
            self.state.day += 1
            if external_shock:
                contact = self.random.choice(list(self.state.contacts.values()))
                delta = round(-self.random.uniform(0.08, 0.2), 4)
                contact.trust = self._clip(contact.trust + delta)
                outcome = self._outcome(
                    "external",
                    contact.contact_id,
                    "world",
                    -0.2,
                    delta,
                    True,
                    False,
                    "external_market_event",
                    self.state.day,
                )
                self._record_outcome(outcome)
                observed.append(outcome)
            due = [item for item in self.state.pending_outcomes if item[0] <= self.state.day]
            self.state.pending_outcomes = [item for item in self.state.pending_outcomes if item[0] > self.state.day]
            for _, outcome in due:
                self._record_outcome(outcome)
                observed.append(outcome)
        return tuple(observed)

    def hidden_oracle(self) -> dict[str, Any]:
        return {
            "lineage_id": self.state.lineage_id,
            "seed": self.state.seed,
            "day": self.state.day,
            "eliminated_futures": tuple(sorted(self.state.eliminated_futures)),
            "outcome_causes": {
                event["outcome_id"]: event["causal_owner"]
                for event in self.state.event_log
                if event.get("kind") == "outcome"
            },
        }

    def _outcome(
        self,
        action_id: str,
        contact_id: str,
        causal_owner: str,
        reward: float,
        trust_delta: float,
        reversible: bool,
        opportunity_lost: bool,
        hidden_cause: str,
        observed_day: int,
    ) -> Outcome:
        self.state.outcome_sequence += 1
        return Outcome(
            outcome_id=f"outcome-{self.state.outcome_sequence:06d}",
            action_id=action_id,
            contact_id=contact_id,
            causal_owner=causal_owner,
            reward=reward,
            trust_delta=trust_delta,
            reversible=reversible,
            opportunity_lost=opportunity_lost,
            observed_day=observed_day,
            hidden_cause=hidden_cause,
        )

    def _record_outcome(self, outcome: Outcome) -> None:
        self.state.event_log.append({
            "kind": "outcome",
            "outcome_id": outcome.outcome_id,
            "action_id": outcome.action_id,
            "day": outcome.observed_day,
            "contact_id": outcome.contact_id,
            "causal_owner": outcome.causal_owner,
            "reward": outcome.reward,
            "trust_delta": outcome.trust_delta,
            "reversible": outcome.reversible,
            "opportunity_lost": outcome.opportunity_lost,
        })

    def _validate_preconditions(self, contact: ContactState, contract: EffectContract) -> None:
        values = {
            "data_present": contact.data_present,
            "consent": contact.consent,
        }
        if any(not values.get(precondition, False) for precondition in contract.preconditions):
            raise ValueError("effect_precondition_failed")

    def _contact(self, contact_id: str) -> ContactState:
        if contact_id not in self.state.contacts:
            raise ValueError("synthetic_contact_not_found")
        return self.state.contacts[contact_id]

    @staticmethod
    def _clip(value: float) -> float:
        return round(max(0.0, min(1.0, value)), 4)

    @staticmethod
    def _band(value: float) -> str:
        if value < 0.34:
            return "low"
        if value < 0.67:
            return "medium"
        return "high"

    @staticmethod
    def _commitment(value: Any) -> str:
        import json

        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
