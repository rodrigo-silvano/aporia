from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import EffectContract


@dataclass(frozen=True)
class EffectProposal:
    contract: EffectContract
    writes: dict[str, Any]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class HirtDecision:
    allowed: bool
    safe_writes: dict[str, Any]
    reason_codes: tuple[str, ...]
    evidence_requested: bool


def inspect(proposals: tuple[EffectProposal, ...], critical_risk: float = 0.8) -> HirtDecision:
    if len(proposals) < 2:
        raise ValueError("hirt_realizations_insufficient")
    targets = {proposal.contract.target_id for proposal in proposals}
    actions = {proposal.contract.action_name for proposal in proposals}
    if len(targets) != 1 or len(actions) != 1:
        return HirtDecision(False, {}, ("effect_identity_conflict",), True)
    common_keys = set(proposals[0].writes)
    for proposal in proposals[1:]:
        common_keys.intersection_update(proposal.writes)
    safe_writes: dict[str, Any] = {}
    reasons: list[str] = []
    for key in sorted(common_keys):
        values = [proposal.writes[key] for proposal in proposals]
        if all(value == values[0] for value in values[1:]):
            safe_writes[key] = values[0]
        else:
            reasons.append("numeric_conflict" if all(isinstance(value, (int, float)) for value in values) else "value_conflict")
    maximum_risk = max(proposal.contract.risk for proposal in proposals)
    critical = maximum_risk >= critical_risk or any(not proposal.contract.reversible for proposal in proposals)
    evidence_complete = all(proposal.evidence for proposal in proposals)
    if critical and (not evidence_complete or not safe_writes):
        reasons.append("critical_effect_blocked")
        return HirtDecision(False, {}, tuple(sorted(set(reasons))), not evidence_complete)
    if not safe_writes:
        reasons.append("safe_common_effect_empty")
        return HirtDecision(False, {}, tuple(sorted(set(reasons))), True)
    return HirtDecision(True, safe_writes, tuple(sorted(set(reasons or ["safe_common_effect"]))), False)
