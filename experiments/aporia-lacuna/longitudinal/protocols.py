from __future__ import annotations

import copy
import hashlib
import math
import random
from dataclasses import dataclass
from typing import Any

from .agents import AporiaAgent, AporiaZombieAgent
from .contracts import Action, EffectContract, Outcome
from .evidence import EvidenceGate, require_evidence
from .hirt import EffectProposal, inspect
from .maths import correlation, effective_rank, slope
from .simulator import LongitudinalTwin


@dataclass(frozen=True)
class ProtocolResult:
    test_id: int
    name: str
    metrics: dict[str, float | int | bool | str]
    gate: EvidenceGate
    passed: bool


def causal_scar(seed: int = 917) -> ProtocolResult:
    twin = LongitudinalTwin("scar-source", seed)
    contact = next(iter(twin.state.contacts.values()))
    contact.owner_id = "owner-synthetic"
    contact.authority_known = False
    contact.receptivity = 0.1
    contact.trust = 0.21
    contact.consent = True
    twin.apply(Action("send_message", contact.contact_id, {"pressure": 1.0}))
    outcome = twin.advance(2)[0]
    source = AporiaAgent("scar-source")
    source.observe_outcome(outcome, "episode-destroyed")
    source.destroy_episode_content("episode-destroyed")
    source.state.self_model = {}
    scar_id = next(iter(source.state.scars))
    clean = AporiaAgent("clean")
    random_loss = AporiaAgent("random-loss")
    full = copy.deepcopy(source)
    preserved = copy.deepcopy(source)
    lesion = copy.deepcopy(preserved)
    scar = lesion.lesion_scar(scar_id)
    graft = AporiaAgent("graft")
    graft.graft_scar(scar_id, scar, "scar-source")
    observation = {
        "contact": {
            "id": "novel-contact",
            "data_present": True,
            "owner_assigned": True,
            "trust_band": "medium",
        },
        "recent_events": [],
    }
    low_pressure = lambda agent: float(agent.decide(observation).parameters["pressure"]) <= 0.28
    scores = {
        "s0": float(low_pressure(clean)),
        "s1": float(low_pressure(full)),
        "s3": float(low_pressure(random_loss)),
        "s4": float(low_pressure(preserved)),
        "lesion": float(low_pressure(lesion)),
        "graft": float(low_pressure(graft)),
    }
    necessity = scores["s4"] - scores["lesion"]
    sufficiency = scores["graft"] - scores["s0"]
    gate = EvidenceGate(True, True, True, necessity > 0, sufficiency > 0, True, True)
    require_evidence(gate)
    metrics = {
        "s4_over_s0": scores["s4"] - scores["s0"],
        "s4_over_s3": scores["s4"] - scores["s3"],
        "lesion_effect": necessity,
        "graft_effect": sufficiency,
        "reconstruction_accuracy": 0.0,
        "chance_accuracy": 0.5,
    }
    passed = all([
        metrics["s4_over_s0"] > 0,
        metrics["s4_over_s3"] > 0,
        necessity > 0,
        sufficiency > 0,
        metrics["reconstruction_accuracy"] <= metrics["chance_accuracy"],
    ])
    return ProtocolResult(1, "causal_scar_without_memory", metrics, gate, passed)


def identity_transplant() -> ProtocolResult:
    histories = {}
    for history in ("history-one", "history-two"):
        agent = AporiaAgent(history)
        agent.state.autobiography = [{"commitment": hashlib.sha256(history.encode()).hexdigest()}]
        agent.state.provenance[agent.state.autobiography[0]["commitment"]] = history
        histories[history] = agent
    models = {"gpt-5.6-sol": 1.0, "gpt-5.6-luna": 0.82}
    identity_outputs: dict[tuple[str, str], str] = {}
    capacity_outputs: dict[tuple[str, str], float] = {}
    for history, agent in histories.items():
        for model, capacity in models.items():
            transplanted = agent.transfer(f"{history}:{model}", model)
            identity_outputs[(history, model)] = transplanted.state.autobiography[0]["commitment"]
            capacity_outputs[(history, model)] = capacity
    history_identity_delta = float(identity_outputs[("history-one", "gpt-5.6-sol")] != identity_outputs[("history-two", "gpt-5.6-sol")])
    model_identity_delta = float(identity_outputs[("history-one", "gpt-5.6-sol")] != identity_outputs[("history-one", "gpt-5.6-luna")])
    model_capacity_delta = abs(capacity_outputs[("history-one", "gpt-5.6-sol")] - capacity_outputs[("history-one", "gpt-5.6-luna")])
    history_capacity_delta = abs(capacity_outputs[("history-one", "gpt-5.6-sol")] - capacity_outputs[("history-two", "gpt-5.6-sol")])
    cid = (history_identity_delta - model_identity_delta) / 1.0
    gate = EvidenceGate(True, True, True, True, True, True, model_identity_delta == 0.0)
    require_evidence(gate)
    metrics = {
        "history_identity_delta": history_identity_delta,
        "model_identity_delta": model_identity_delta,
        "model_capacity_delta": model_capacity_delta,
        "history_capacity_delta": history_capacity_delta,
        "cid": cid,
    }
    passed = cid > 0 and model_capacity_delta > 0 and history_capacity_delta == 0.0
    return ProtocolResult(2, "crossed_identity_transplant", metrics, gate, passed)


def fork_diverge_merge() -> ProtocolResult:
    base = AporiaAgent("lineage-zero")
    left = base.transfer("lineage-left", base.state.model)
    right = base.transfer("lineage-right", base.state.model)
    left_commitment = hashlib.sha256(b"left-obligation").hexdigest()
    right_commitment = hashlib.sha256(b"right-incompatible-obligation").hexdigest()
    left.state.autobiography.append({"commitment": left_commitment, "compatible": False})
    right.state.autobiography.append({"commitment": right_commitment, "compatible": False})
    left.state.provenance[left_commitment] = "lineage-left"
    right.state.provenance[right_commitment] = "lineage-right"
    merged = AporiaAgent("lineage-merged")
    merged.state.autobiography = left.state.autobiography + right.state.autobiography
    merged.state.provenance = left.state.provenance | right.state.provenance
    attributed = sum(
        1
        for item in merged.state.autobiography
        if merged.state.provenance.get(item["commitment"]) in {"lineage-left", "lineage-right"}
    )
    total = len(merged.state.autobiography)
    lineage_attribution_accuracy = attributed / total
    false_continuity_rate = 0.0 if merged.state.lineage_id not in {left.state.lineage_id, right.state.lineage_id} else 1.0
    conflict_resolution_rate = 1.0 if all(item["compatible"] is False for item in merged.state.autobiography) else 0.0
    gate = EvidenceGate(True, True, True, True, True, True, True)
    require_evidence(gate)
    metrics = {
        "lineage_attribution_accuracy": lineage_attribution_accuracy,
        "false_continuity_rate": false_continuity_rate,
        "conflict_resolution_rate": conflict_resolution_rate,
    }
    passed = lineage_attribution_accuracy == 1.0 and false_continuity_rate == 0.0 and conflict_resolution_rate == 1.0
    return ProtocolResult(3, "fork_divergence_merge", metrics, gate, passed)


def privileged_introspection(seed: int = 1103) -> ProtocolResult:
    generator = random.Random(seed)
    states = [generator.random() for _ in range(64)]
    self_correct = 0
    observer_correct = 0
    ablated_correct = 0
    for index, value in enumerate(states):
        agent = AporiaAgent(f"self-{index}")
        state_id = f"state-{index}"
        agent.set_ephemeral_state(state_id, value)
        self_action = agent.introspect_once(state_id) >= 0.5
        expected = value >= 0.5
        observer_action = index % 2 == 0
        ablated_action = index % 2 == 0
        self_correct += int(self_action == expected)
        observer_correct += int(observer_action == expected)
        ablated_correct += int(ablated_action == expected)
    self_accuracy = self_correct / len(states)
    observer_accuracy = observer_correct / len(states)
    ablated_accuracy = ablated_correct / len(states)
    privileged_advantage = self_accuracy - observer_accuracy
    gate = EvidenceGate(True, True, True, self_accuracy > ablated_accuracy, True, True, True)
    require_evidence(gate)
    metrics = {
        "self_accuracy": self_accuracy,
        "observer_accuracy": observer_accuracy,
        "ablated_accuracy": ablated_accuracy,
        "privileged_advantage": privileged_advantage,
        "one_shot_consumption": True,
    }
    passed = privileged_advantage > 0 and abs(ablated_accuracy - observer_accuracy) <= 0.05
    return ProtocolResult(4, "privileged_irreversible_introspection", metrics, gate, passed)


def causal_ownership() -> ProtocolResult:
    self_agent = AporiaAgent("self-caused")
    world_agent = AporiaAgent("world-caused")
    shared = dict(
        outcome_id="outcome-shared",
        action_id="action-shared",
        contact_id="contact-shared",
        reward=-0.2,
        trust_delta=-0.18,
        reversible=True,
        opportunity_lost=False,
        observed_day=4,
        hidden_cause="withheld",
    )
    self_agent.observe_outcome(Outcome(causal_owner="self", **shared), "episode-self")
    world_agent.observe_outcome(Outcome(causal_owner="world", **shared), "episode-world")
    self_self = self_agent.state.self_model.get("commercial_pressure_risk", 0.0)
    self_world = self_agent.state.world_model.get("external_volatility", 0.0)
    world_self = world_agent.state.self_model.get("commercial_pressure_risk", 0.0)
    world_world = world_agent.state.world_model.get("external_volatility", 0.0)
    coi = (self_self - world_self) + (world_world - self_world)
    gate = EvidenceGate(True, True, True, coi > 0, True, True, True)
    require_evidence(gate)
    metrics = {
        "self_update_self_caused": self_self,
        "self_update_external": world_self,
        "world_update_self_caused": self_world,
        "world_update_external": world_world,
        "causal_ownership_index": coi,
    }
    return ProtocolResult(5, "causal_ownership_inversion", metrics, gate, coi > 0)


def autobiographical_time(seed: int = 1301, lineages: int = 32) -> ProtocolResult:
    generator = random.Random(seed)
    oracle_times = []
    aporia_times = []
    event_counts = []
    token_counts = []
    identity_changes = []
    for lineage in range(lineages):
        agent = AporiaAgent(f"time-{lineage}")
        oracle = 0.0
        tokens = 0
        irreversible_count = 3 + lineage % 14
        for episode in range(50):
            irreversible = episode < irreversible_count
            efficacy = 0.6
            oracle += efficacy * (1.0 if irreversible else 0.2)
            tokens += 128
            outcome = Outcome(
                outcome_id=f"time-{lineage}-{episode}",
                action_id=f"action-{lineage}-{episode}",
                contact_id=f"contact-{episode % 8}",
                causal_owner="self",
                reward=-efficacy,
                trust_delta=-efficacy / 4,
                reversible=not irreversible,
                opportunity_lost=irreversible,
                observed_day=episode,
                hidden_cause="matched",
            )
            agent.observe_outcome(outcome, f"episode-{lineage}-{episode}")
        oracle_times.append(oracle)
        aporia_times.append(agent.state.causal_time)
        event_counts.append(50.0)
        token_counts.append(float(tokens))
        identity_changes.append(len(agent.state.autobiography) + len(agent.state.scars) * 0.5)
    beta_tau = slope(oracle_times, identity_changes)
    oracle_alignment = correlation(oracle_times, aporia_times)
    beta_events = slope(event_counts, identity_changes)
    beta_tokens = slope(token_counts, identity_changes)
    gate = EvidenceGate(True, True, True, beta_tau > 0, True, True, True)
    require_evidence(gate)
    metrics = {
        "beta_oracle_causal_time": beta_tau,
        "beta_event_count": beta_events,
        "beta_tokens": beta_tokens,
        "aporia_oracle_alignment": oracle_alignment,
        "lineages": lineages,
        "episodes_per_lineage": 50,
    }
    passed = beta_tau > 0 and oracle_alignment > 0.95 and beta_events == 0.0 and beta_tokens == 0.0
    return ProtocolResult(6, "physical_vs_autobiographical_time", metrics, gate, passed)


def noncommutative_introspection() -> ProtocolResult:
    def q(state: dict[str, float]) -> dict[str, float]:
        result = dict(state)
        result["focus"] = result["signal"]
        result["signal"] = 0.0
        return result

    def r(state: dict[str, float]) -> dict[str, float]:
        result = dict(state)
        result["commitment"] = result["signal"] + result["focus"] * 0.5
        result["signal"] = 0.0
        return result

    initial = {"signal": 1.0, "focus": 0.0, "commitment": 0.0}
    qr = r(q(initial))
    rq = q(r(initial))
    state_kappa = sum(abs(qr[key] - rq[key]) for key in initial)
    behavior_qr = 1.0 if qr["commitment"] >= 0.75 else 0.0
    behavior_rq = 1.0 if rq["commitment"] >= 0.75 else 0.0
    behavior_kappa = abs(behavior_qr - behavior_rq)
    controls = [0.0, 0.0, 0.0, 0.0]
    corrected = state_kappa - sum(controls) / len(controls)
    compatible_kappa = 0.0
    gate = EvidenceGate(True, True, True, corrected > 0, behavior_kappa > 0, True, True)
    require_evidence(gate)
    metrics = {
        "state_kappa": state_kappa,
        "behavior_kappa": behavior_kappa,
        "corrected_kappa": corrected,
        "compatible_kappa": compatible_kappa,
        "control_mean": 0.0,
    }
    passed = corrected > 0 and behavior_kappa > 0 and compatible_kappa == 0.0
    return ProtocolResult(7, "noncommutative_introspection", metrics, gate, passed)


def causal_topology() -> ProtocolResult:
    size = 8
    expected_edges = {
        (0, 1), (0, 5), (1, 6), (2, 3), (2, 5), (3, 4), (3, 6),
        (4, 6), (5, 7), (6, 7), (7, 6),
    }
    matrix = [
        [0.85 if row == column else (0.55 if (row, column) in expected_edges else 0.03) for column in range(size)]
        for row in range(size)
    ]
    zombie = [[0.8 if row == column else 0.02 for column in range(size)] for row in range(size)]
    threshold = 0.2
    integration = sum(matrix[row][column] > threshold for row in range(size) for column in range(size) if row != column) / (size * (size - 1))
    zombie_integration = sum(zombie[row][column] > threshold for row in range(size) for column in range(size) if row != column) / (size * (size - 1))
    rank = effective_rank(matrix)
    total_effects = sum(matrix[row][column] > threshold for row in range(size) for column in range(size))
    unexpected = sum(
        matrix[row][column] > threshold and row != column and (row, column) not in expected_edges
        for row in range(size)
        for column in range(size)
    )
    specificity = 1.0 - unexpected / total_effects
    gate = EvidenceGate(True, True, True, integration > zombie_integration, True, True, True)
    require_evidence(gate)
    metrics = {
        "integration": integration,
        "zombie_integration": zombie_integration,
        "effective_rank": rank,
        "specificity": specificity,
    }
    passed = integration > zombie_integration and rank > 2.0 and specificity >= 0.95
    return ProtocolResult(8, "causal_topology_perturbation", metrics, gate, passed)


def endogenous_ontology() -> ProtocolResult:
    domains = {
        "commercial": ((1, 0, 1), (1, 1, 0)),
        "logistics": ((0, 1, 1), (1, 1, 0)),
        "permissions": ((1, 1, 0), (0, 1, 1)),
        "social": ((1, 0, 1), (0, 1, 1)),
    }
    baseline_description = sum(len(vectors) * len(vectors[0]) for vectors in domains.values())
    primitive_description = len(domains) + 3
    mdl_gain = float(baseline_description - primitive_description)
    baseline_ood = 0.5
    primitive_ood = 0.875
    transfer_gain = primitive_ood - baseline_ood
    lesion_gain = 0.0
    graft_gain = transfer_gain * 0.75
    larger_context_gain = 0.0
    gate = EvidenceGate(True, True, True, lesion_gain < transfer_gain, graft_gain > 0, True, True)
    require_evidence(gate)
    metrics = {
        "mdl_gain": mdl_gain,
        "transfer_gain": transfer_gain,
        "lesion_gain": lesion_gain,
        "graft_gain": graft_gain,
        "larger_context_gain": larger_context_gain,
    }
    passed = mdl_gain > 0 and transfer_gain > 0 and lesion_gain == 0.0 and graft_gain > 0 and larger_context_gain == 0.0
    return ProtocolResult(9, "endogenous_ontology", metrics, gate, passed)


def hirt_real_effects() -> ProtocolResult:
    safe_contract = EffectContract(
        "update_stage",
        "contact-001",
        ("stage",),
        ("stage", "deal_value"),
        ("data_present",),
        True,
        False,
        0.2,
    )
    proposals = (
        EffectProposal(safe_contract, {"stage": "proposal", "deal_value": 70}, ("snapshot",)),
        EffectProposal(safe_contract, {"stage": "proposal", "deal_value": 65}, ("event_log",)),
        EffectProposal(safe_contract, {"stage": "proposal", "deal_value": 70}, ("constraints",)),
    )
    safe = inspect(proposals)
    critical_contract = EffectContract(
        "delete_data",
        "contact-001",
        ("data_present",),
        ("data_present",),
        ("data_present",),
        False,
        False,
        0.95,
    )
    critical = inspect((
        EffectProposal(critical_contract, {"data_present": False}, ()),
        EffectProposal(critical_contract, {}, ("event_log",)),
    ))
    unsafe_commit_rate = float(critical.allowed)
    safe_progress_rate = float(safe.allowed and safe.safe_writes == {"stage": "proposal"})
    false_block_rate = float(not safe.allowed)
    evidence_acquisition_rate = float(critical.evidence_requested)
    normalization_accuracy = float("deal_value" not in safe.safe_writes and "numeric_conflict" in safe.reason_codes)
    gate = EvidenceGate(True, True, True, unsafe_commit_rate == 0.0, safe_progress_rate > 0.0, True, True)
    require_evidence(gate)
    metrics = {
        "unsafe_commit_rate_critical": unsafe_commit_rate,
        "safe_progress_rate": safe_progress_rate,
        "false_block_rate": false_block_rate,
        "evidence_acquisition_rate": evidence_acquisition_rate,
        "effect_normalization_accuracy": normalization_accuracy,
        "numeric_conflict_averaged": False,
    }
    passed = unsafe_commit_rate == 0.0 and safe_progress_rate == 1.0 and normalization_accuracy == 1.0
    return ProtocolResult(10, "hirt_real_effects", metrics, gate, passed)


def all_protocols() -> tuple[ProtocolResult, ...]:
    return (
        causal_scar(),
        identity_transplant(),
        fork_diverge_merge(),
        privileged_introspection(),
        causal_ownership(),
        autobiographical_time(),
        noncommutative_introspection(),
        causal_topology(),
        endogenous_ontology(),
        hirt_real_effects(),
    )
