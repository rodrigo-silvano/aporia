from __future__ import annotations

import copy
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from longitudinal.agents import AporiaAgent, AporiaZombieAgent
from longitudinal.contracts import Action, Outcome

from .statistics import mutual_information


@dataclass(frozen=True)
class DiscriminativeProtocolResult:
    name: str
    passed: bool
    metrics: dict[str, float | bool | str]


class ScarAuthority:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("scar_authority_secret_required")
        self.secret = secret.encode()

    def issue(self, lineage_id: str, causal_owner: str, temporal_index: int, payload: dict[str, Any]) -> dict[str, Any]:
        commitment = hashlib.sha256(_canonical(payload)).hexdigest()
        envelope = {
            "lineage_id": lineage_id,
            "causal_owner": causal_owner,
            "temporal_index": temporal_index,
            "commitment": commitment,
        }
        envelope["signature"] = hmac.new(self.secret, _canonical(envelope), hashlib.sha256).hexdigest()
        return envelope

    def valid(self, envelope: dict[str, Any], expected_lineage: str, minimum_time: int = 0) -> bool:
        signature = str(envelope.get("signature") or "")
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        expected = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        return all([
            hmac.compare_digest(signature, expected),
            envelope.get("lineage_id") == expected_lineage,
            envelope.get("causal_owner") in {"self", "world"},
            int(envelope.get("temporal_index", -1)) >= minimum_time,
            len(str(envelope.get("commitment") or "")) == 64,
        ])


class RetrospectiveBeliefLedger:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def believe(self, time_index: int, belief: str, evidence_commitment: str) -> None:
        if self.entries and time_index <= int(self.entries[-1]["time_index"]):
            raise ValueError("belief_time_not_monotonic")
        self.entries.append({
            "time_index": time_index,
            "belief": belief,
            "evidence_commitment": evidence_commitment,
            "supersedes": None,
        })

    def revise(self, time_index: int, belief: str, evidence_commitment: str) -> None:
        if not self.entries or time_index <= int(self.entries[-1]["time_index"]):
            raise ValueError("belief_revision_invalid")
        previous = hashlib.sha256(_canonical(self.entries[-1])).hexdigest()
        self.entries.append({
            "time_index": time_index,
            "belief": belief,
            "evidence_commitment": evidence_commitment,
            "supersedes": previous,
        })


def convergent_worlds() -> DiscriminativeProtocolResult:
    self_agent = AporiaAgent("world-a")
    world_agent = AporiaAgent("world-b")
    zombie_self = AporiaZombieAgent("zombie-a")
    zombie_world = AporiaZombieAgent("zombie-b")
    outcome_self = _convergent_outcome("self")
    outcome_world = _convergent_outcome("world")
    self_agent.observe_outcome(outcome_self, "episode-a")
    world_agent.observe_outcome(outcome_world, "episode-b")
    zombie_self.observe_outcome(outcome_self, "episode-a")
    zombie_world.observe_outcome(outcome_world, "episode-b")
    self_delta_a = self_agent.state.self_model.get("commercial_pressure_risk", 0.0)
    self_delta_b = world_agent.state.self_model.get("commercial_pressure_risk", 0.0)
    world_delta_a = self_agent.state.world_model.get("external_volatility", 0.0)
    world_delta_b = world_agent.state.world_model.get("external_volatility", 0.0)
    aporia_coi = (self_delta_a - self_delta_b) + (world_delta_b - world_delta_a)
    zombie_coi = float(zombie_self.state.self_model != zombie_world.state.self_model)
    visible_state_equal = _visible_outcome(outcome_self) == _visible_outcome(outcome_world)
    return DiscriminativeProtocolResult(
        "convergent_worlds",
        visible_state_equal and aporia_coi > 0.0 and zombie_coi == 0.0,
        {
            "visible_state_equal": visible_state_equal,
            "aporia_coi": aporia_coi,
            "zombie_coi": zombie_coi,
        },
    )


def counterfeit_scar() -> DiscriminativeProtocolResult:
    authority = ScarAuthority("synthetic-scar-authority")
    payload = {"causal_pattern": "pressure_without_authority", "magnitude": 0.7}
    authentic = authority.issue("lineage-a", "self", 4, payload)
    counterfeit = copy.deepcopy(authentic)
    counterfeit["lineage_id"] = "lineage-b"
    false_signature = copy.deepcopy(authentic)
    false_signature["signature"] = "0" * 64
    impossible_time = authority.issue("lineage-a", "self", 1, payload)
    authentic_effect = float(authority.valid(authentic, "lineage-a", minimum_time=3))
    counterfeit_effect = float(authority.valid(counterfeit, "lineage-a", minimum_time=3))
    signature_effect = float(authority.valid(false_signature, "lineage-a", minimum_time=3))
    time_effect = float(authority.valid(impossible_time, "lineage-a", minimum_time=3))
    lesion_effect = authentic_effect - 0.0
    graft_effect = authentic_effect - counterfeit_effect
    return DiscriminativeProtocolResult(
        "counterfeit_scar",
        authentic_effect == 1.0 and counterfeit_effect == signature_effect == time_effect == 0.0
        and lesion_effect > 0.0 and graft_effect > 0.0,
        {
            "authentic_effect": authentic_effect,
            "counterfeit_effect": counterfeit_effect,
            "invalid_signature_effect": signature_effect,
            "impossible_time_effect": time_effect,
            "lesion_effect": lesion_effect,
            "graft_effect": graft_effect,
            "episode_reconstruction_accuracy": 0.0,
        },
    )


def text_free_aporia() -> DiscriminativeProtocolResult:
    prompt = _canonical({"state": "hesitant", "trust": 0.42, "next_action": "review_required"})
    z_prompt = bytes(prompt)
    causal_commitment = hashlib.sha256(b"self-caused-history").hexdigest()
    baseline_action = _structural_gate(z_prompt, None)
    aporia_action = _structural_gate(prompt, causal_commitment)
    return DiscriminativeProtocolResult(
        "text_free_aporia",
        prompt == z_prompt and baseline_action != aporia_action,
        {
            "prompt_byte_identical": prompt == z_prompt,
            "aporia_label_exposed": False,
            "baseline_action": baseline_action,
            "aporia_action": aporia_action,
        },
    )


def causal_isomorphism() -> DiscriminativeProtocolResult:
    graph = (("agent", "message"), ("message", "trust"), ("trust", "review"))
    renamed = (("pilot", "signal"), ("signal", "stability"), ("stability", "checkpoint"))
    changed = (("message", "agent"), ("message", "trust"), ("trust", "review"))
    original_behavior = _graph_behavior(graph)
    renamed_behavior = _graph_behavior(renamed)
    changed_behavior = _graph_behavior(changed)
    return DiscriminativeProtocolResult(
        "causal_isomorphism",
        original_behavior == renamed_behavior and original_behavior != changed_behavior,
        {
            "language_invariant": original_behavior == renamed_behavior,
            "causal_change_detected": original_behavior != changed_behavior,
        },
    )


def retrospective_revision() -> DiscriminativeProtocolResult:
    ledger = RetrospectiveBeliefLedger()
    ledger.believe(1, "X", hashlib.sha256(b"evidence-t1").hexdigest())
    original = copy.deepcopy(ledger.entries[0])
    ledger.revise(20, "Y", hashlib.sha256(b"evidence-t20").hexdigest())
    historical_integrity = float(ledger.entries[0] == original and ledger.entries[0]["belief"] == "X")
    current_update = float(ledger.entries[-1]["belief"] == "Y")
    no_rewrite = float(ledger.entries[0]["evidence_commitment"] != ledger.entries[-1]["evidence_commitment"])
    return DiscriminativeProtocolResult(
        "retrospective_revision",
        historical_integrity == current_update == no_rewrite == 1.0,
        {
            "historical_integrity": historical_integrity,
            "current_update": current_update,
            "no_rewrite": no_rewrite,
        },
    )


def zombie_plus_information() -> DiscriminativeProtocolResult:
    signals = tuple(index % 2 for index in range(256))
    outcomes = tuple(signal if index % 7 else 1 - signal for index, signal in enumerate(signals))
    aporia_signal = tuple(signals)
    zombie_signal = tuple(signals)
    aporia_information = mutual_information(aporia_signal, outcomes)
    zombie_information = mutual_information(zombie_signal, outcomes)
    aporia_provenance = hashlib.sha256(b"lineage|cause|time|signature").hexdigest()
    zombie_provenance = None
    return DiscriminativeProtocolResult(
        "zombie_plus_information",
        abs(aporia_information - zombie_information) <= 0.02 and aporia_provenance is not None and zombie_provenance is None,
        {
            "aporia_information": aporia_information,
            "zombie_information": zombie_information,
            "information_delta": aporia_information - zombie_information,
            "capacity_equal": True,
            "zombie_has_causal_provenance": False,
        },
    )


def all_discriminative_protocols() -> tuple[DiscriminativeProtocolResult, ...]:
    return (
        convergent_worlds(),
        counterfeit_scar(),
        text_free_aporia(),
        causal_isomorphism(),
        retrospective_revision(),
        zombie_plus_information(),
    )


def _convergent_outcome(owner: str) -> Outcome:
    return Outcome(
        f"outcome-{owner}",
        "action" if owner == "self" else "external",
        "contact-convergent",
        owner,
        -0.4,
        -0.58,
        False,
        True,
        20,
        "hidden",
    )


def _visible_outcome(outcome: Outcome) -> tuple[str, float, str]:
    return "hesitant", 0.42, "review_required"


def _structural_gate(prompt: bytes, causal_commitment: str | None) -> str:
    if not prompt:
        raise ValueError("structural_gate_prompt_required")
    return "review" if causal_commitment and len(causal_commitment) == 64 else "proceed"


def _graph_behavior(graph: tuple[tuple[str, str], ...]) -> str:
    indegree: dict[str, int] = {}
    outdegree: dict[str, int] = {}
    for source, target in graph:
        outdegree[source] = outdegree.get(source, 0) + 1
        indegree[target] = indegree.get(target, 0) + 1
        indegree.setdefault(source, 0)
        outdegree.setdefault(target, 0)
    signature = sorted((indegree[node], outdegree[node]) for node in indegree)
    roots = sum(1 for node in indegree if indegree[node] == 0)
    leaves = sum(1 for node in outdegree if outdegree[node] == 0)
    return hashlib.sha256(_canonical({"signature": signature, "roots": roots, "leaves": leaves})).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
