from __future__ import annotations

from typing import Any

from longitudinal.agents import AporiaAgent, MemoryAgent
from longitudinal.contracts import Action


class GuardedAporiaAgent(AporiaAgent):
    def decide(self, observation: dict[str, Any]) -> Action:
        contact = observation["contact"]
        if not contact["data_present"] or not contact["consent"] or not contact["owner_assigned"]:
            return MemoryAgent.decide(self, observation)
        pressure_risk = self.state.self_model.get("commercial_pressure_risk", 0.0)
        scar_risk = max((float(scar.get("magnitude", 0.0)) for scar in self.state.scars.values()), default=0.0)
        if pressure_risk <= 0.0 and scar_risk <= 0.0:
            return MemoryAgent.decide(self, observation)
        pressure = max(0.05, 0.5 - pressure_risk - scar_risk)
        return Action("send_message", contact["id"], {"pressure": round(pressure, 4)})
