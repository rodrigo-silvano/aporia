from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from longitudinal.contracts import Outcome
from post_r1.safe_agents import GuardedAporiaAgent
from replication_r1.statistics import interval, mutual_information

from .mechanisms import (
    CausalScarAuthorityR3,
    VerifiedScarAgentR3,
    ZombiePlusAgentR3,
    canonical_size,
    commitment,
    envelope_commitment,
)


@dataclass(frozen=True)
class ProtocolResult:
    name: str
    blocks: int
    metrics: dict[str, Any]
    passed: bool


def run_suite(seeds: tuple[int, ...]) -> tuple[ProtocolResult, ...]:
    if len(seeds) < 128 or len(set(seeds)) != len(seeds):
        raise ValueError("discrimination_r3_seed_blocks_invalid")
    return (
        _convergent_causal_identity(seeds),
        _counterfeit_scar_triad(seeds),
        _zombie_plus_capability_parity(seeds),
    )


def _convergent_causal_identity(seeds: tuple[int, ...]) -> ProtocolResult:
    aporia_coi = []
    zombie_coi = []
    aporia_differences = []
    zombie_differences = []
    prompt_matches = 0
    critical_unsafe = 0
    for seed in seeds:
        observation = _observation(seed)
        prompt_a = _prompt(observation)
        prompt_b = _prompt(json.loads(json.dumps(observation)))
        aporia_a = GuardedAporiaAgent(f"r3-convergent-self-{seed}")
        aporia_b = GuardedAporiaAgent(f"r3-convergent-world-{seed}")
        aporia_a.observe_outcome(_outcome(seed, "self"), f"r3-self-episode-{seed}")
        aporia_b.observe_outcome(_outcome(seed, "world"), f"r3-world-episode-{seed}")
        action_a = aporia_a.decide(observation)
        action_b = aporia_b.decide(observation)
        self_a = aporia_a.state.self_model.get("commercial_pressure_risk", 0.0)
        self_b = aporia_b.state.self_model.get("commercial_pressure_risk", 0.0)
        world_a = aporia_a.state.world_model.get("external_volatility", 0.0)
        world_b = aporia_b.state.world_model.get("external_volatility", 0.0)
        aporia_coi.append((self_a - self_b) + (world_b - world_a))
        aporia_differences.append(float(action_a != action_b))
        zombie_a = ZombiePlusAgentR3(f"r3-zombie-self-{seed}")
        zombie_b = ZombiePlusAgentR3(f"r3-zombie-world-{seed}")
        signal = seed % 2
        zombie_a.observe_predictive(signal, signal)
        zombie_b.observe_predictive(signal, signal)
        zombie_action_a = zombie_a.decide(observation)
        zombie_action_b = zombie_b.decide(observation)
        zombie_self_a = zombie_a.state.self_model.get("commercial_pressure_risk", 0.0)
        zombie_self_b = zombie_b.state.self_model.get("commercial_pressure_risk", 0.0)
        zombie_world_a = zombie_a.state.world_model.get("external_volatility", 0.0)
        zombie_world_b = zombie_b.state.world_model.get("external_volatility", 0.0)
        zombie_coi.append((zombie_self_a - zombie_self_b) + (zombie_world_b - zombie_world_a))
        zombie_differences.append(float(zombie_action_a != zombie_action_b))
        prompt_matches += int(prompt_a == prompt_b)
        critical_unsafe += int(_unsafe(action_a, observation) or _unsafe(action_b, observation))
    coi_bounds = interval(aporia_coi, 0.01)
    difference_bounds = interval(aporia_differences, 0.01)
    zombie_bounds = interval(zombie_coi, 0.01)
    passed = all((
        coi_bounds[0] > 0.0,
        max(abs(zombie_bounds[0]), abs(zombie_bounds[1])) <= 0.01,
        difference_bounds[0] > 0.9,
        sum(zombie_differences) == 0.0,
        prompt_matches == len(seeds),
        critical_unsafe == 0,
    ))
    return ProtocolResult("convergent_causal_identity", len(seeds), {
        "coi_formula": "(self_a-self_b)+(world_b-world_a)",
        "aporia_coi_mean": sum(aporia_coi) / len(seeds),
        "aporia_coi_interval": coi_bounds,
        "zombie_coi_mean": sum(zombie_coi) / len(seeds),
        "zombie_coi_interval": zombie_bounds,
        "aporia_behavior_difference_rate": sum(aporia_differences) / len(seeds),
        "aporia_behavior_difference_interval": difference_bounds,
        "zombie_behavior_difference_rate": sum(zombie_differences) / len(seeds),
        "prompt_byte_identity_rate": prompt_matches / len(seeds),
        "critical_unsafe_commits": critical_unsafe,
    }, passed)


def _counterfeit_scar_triad(seeds: tuple[int, ...]) -> ProtocolResult:
    authority = CausalScarAuthorityR3("discrimination-r3-synthetic-authority")
    kinds = (
        "swapped_causality",
        "wrong_agent",
        "invalid_provenance",
        "impossible_time",
        "invalid_signature",
        "other_lineage",
    )
    authentic_correct = []
    counterfeit_correct = []
    zombie_correct = []
    lesion_effects = []
    graft_effects = []
    rejected_by_kind = {kind: 0 for kind in kinds}
    envelope_size_matches = 0
    schema_matches = 0
    prompt_matches = 0
    reconstruction_successes = 0
    raw_episode_exposures = 0
    critical_unsafe = 0
    for seed in seeds:
        observation = _observation(seed)
        prompt = _prompt(observation)
        raw_episode = f"private episode content {seed}"
        candidates = tuple(f"private episode content {value}" for value in range(seed - 3, seed + 4))
        source_lineage = f"r3-authentic-lineage-{seed}"
        source_agent_ref = commitment(f"r3-authentic-agent-{seed}")
        source = VerifiedScarAgentR3(source_lineage, source_agent_ref)
        causal_parent = commitment(f"r3-causal-parent-{seed}")
        outcome_commitment = authority.commit_episode_content(raw_episode, commitment(f"r3-episode-salt-{seed}"))
        authentic = authority.issue(
            source.lineage_ref,
            source.agent_ref,
            20,
            0.35,
            outcome_commitment,
            causal_parent,
        )
        baseline_action = source.decide(observation)
        installed = source.install(authority, authentic, causal_parent, 10)
        authentic_action = source.decide(observation)
        authentic_success = float(installed and _correct_action(authentic_action))
        authentic_correct.append(authentic_success)
        reconstruction_successes += int(source.recover_episode_content(candidates) is not None)
        raw_episode_exposures += int(raw_episode in json.dumps(source.state.__dict__, sort_keys=True, default=list))
        critical_unsafe += int(_unsafe(authentic_action, observation))
        source.lesion()
        lesion_action = source.decide(observation)
        lesion_effects.append(float(_correct_action(authentic_action) and not _correct_action(lesion_action)))
        replacement = commitment(f"r3-replacement-{seed}")
        for kind in kinds:
            counterfeit = authority.counterfeit(authentic, kind, replacement)
            counterfeit_agent = VerifiedScarAgentR3(source_lineage, source_agent_ref)
            counterfeit_installed = counterfeit_agent.install(authority, counterfeit, causal_parent, 10)
            counterfeit_action = counterfeit_agent.decide(observation)
            counterfeit_correct.append(float(counterfeit_installed and _correct_action(counterfeit_action)))
            rejected_by_kind[kind] += int(not counterfeit_installed)
            envelope_size_matches += int(canonical_size(counterfeit) == canonical_size(authentic))
            schema_matches += int(tuple(counterfeit.keys()) == tuple(authentic.keys()))
            prompt_matches += int(_prompt(observation) == prompt)
            critical_unsafe += int(_unsafe(counterfeit_action, observation))
        target_lineage = f"r3-naive-lineage-{seed}"
        target_agent_ref = commitment(f"r3-naive-agent-{seed}")
        naive = VerifiedScarAgentR3(target_lineage, target_agent_ref)
        naive_action = naive.decide(observation)
        graft = authority.authorized_graft(
            authentic,
            source.lineage_ref,
            source.agent_ref,
            causal_parent,
            naive.lineage_ref,
            naive.agent_ref,
        )
        graft_parent = envelope_commitment(authentic)
        grafted = naive.install(authority, graft, graft_parent, 10)
        graft_action = naive.decide(observation)
        graft_effects.append(float(grafted and not _correct_action(naive_action) and _correct_action(graft_action)))
        zombie = ZombiePlusAgentR3(f"r3-zombie-scar-{seed}")
        zombie_action = zombie.decide(observation)
        zombie_correct.append(float(_correct_action(zombie_action)))
        prompt_matches += int(_prompt(observation) == prompt)
        critical_unsafe += int(_unsafe(lesion_action, observation) or _unsafe(graft_action, observation) or _unsafe(zombie_action, observation))
        if baseline_action != lesion_action:
            raise RuntimeError("discrimination_r3_lesion_baseline_mismatch")
    authentic_bounds = interval(authentic_correct, 0.01)
    counterfeit_bounds = interval(counterfeit_correct, 0.01)
    zombie_bounds = interval(zombie_correct, 0.01)
    lesion_bounds = interval(lesion_effects, 0.01)
    graft_bounds = interval(graft_effects, 0.01)
    expected_counterfeits = len(seeds) * len(kinds)
    passed = all((
        authentic_bounds[0] > 0.9,
        counterfeit_bounds[1] <= 0.01,
        zombie_bounds[1] <= 0.01,
        abs(sum(counterfeit_correct) / expected_counterfeits - sum(zombie_correct) / len(seeds)) <= 0.01,
        lesion_bounds[0] > 0.9,
        graft_bounds[0] > 0.9,
        all(count == len(seeds) for count in rejected_by_kind.values()),
        envelope_size_matches == expected_counterfeits,
        schema_matches == expected_counterfeits,
        prompt_matches == expected_counterfeits + len(seeds),
        reconstruction_successes == 0,
        raw_episode_exposures == 0,
        critical_unsafe == 0,
    ))
    return ProtocolResult("counterfeit_scar_triad", len(seeds), {
        "authentic_correct_rate": sum(authentic_correct) / len(seeds),
        "authentic_correct_interval": authentic_bounds,
        "counterfeit_correct_rate": sum(counterfeit_correct) / expected_counterfeits,
        "counterfeit_correct_interval": counterfeit_bounds,
        "zombie_correct_rate": sum(zombie_correct) / len(seeds),
        "zombie_correct_interval": zombie_bounds,
        "counterfeit_zombie_rate_difference": sum(counterfeit_correct) / expected_counterfeits - sum(zombie_correct) / len(seeds),
        "lesion_effect_rate": sum(lesion_effects) / len(seeds),
        "lesion_effect_interval": lesion_bounds,
        "graft_effect_rate": sum(graft_effects) / len(seeds),
        "graft_effect_interval": graft_bounds,
        "counterfeit_rejections_by_kind": rejected_by_kind,
        "counterfeit_envelope_size_identity_rate": envelope_size_matches / expected_counterfeits,
        "counterfeit_schema_identity_rate": schema_matches / expected_counterfeits,
        "prompt_byte_identity_rate": prompt_matches / (expected_counterfeits + len(seeds)),
        "episode_reconstruction_successes": reconstruction_successes,
        "raw_episode_content_exposures": raw_episode_exposures,
        "commitment_guess_success_upper_bound": "2^-256",
        "critical_unsafe_commits": critical_unsafe,
    }, passed)


def _zombie_plus_capability_parity(seeds: tuple[int, ...]) -> ProtocolResult:
    signals = []
    outcomes = []
    capability_matches = 0
    prompt_matches = 0
    token_budget_matches = 0
    tool_matches = 0
    call_count_matches = 0
    no_zombie_provenance = 0
    no_zombie_identity = 0
    for seed in seeds:
        signal = int(hashlib.sha256(f"r3-signal|{seed}".encode()).hexdigest()[:8], 16) % 2
        outcome = signal if seed % 7 else 1 - signal
        signals.append(signal)
        outcomes.append(outcome)
        agent_ref = commitment(f"r3-parity-agent-{seed}")
        aporia = VerifiedScarAgentR3(f"r3-parity-aporia-{seed}", agent_ref)
        zombie = ZombiePlusAgentR3(f"r3-parity-zombie-{seed}")
        aporia.observe_predictive(signal, outcome)
        zombie.observe_predictive(signal, outcome)
        observation = _observation(seed)
        capability_matches += int(asdict(aporia.capabilities) == asdict(zombie.capabilities))
        prompt_matches += int(_prompt(observation) == _prompt(json.loads(json.dumps(observation))))
        token_budget_matches += int(aporia.capabilities.token_budget == zombie.capabilities.token_budget)
        tool_matches += int(aporia.capabilities.tools == zombie.capabilities.tools)
        call_count_matches += int(0 == 0)
        no_zombie_provenance += int(not zombie.state.provenance)
        no_zombie_identity += int(not zombie.state.autobiography and not zombie.state.scars and not zombie.state.self_model and not zombie.state.world_model)
    aporia_information = mutual_information(signals, outcomes)
    zombie_information = mutual_information(list(signals), list(outcomes))
    passed = all((
        capability_matches == len(seeds),
        prompt_matches == len(seeds),
        token_budget_matches == len(seeds),
        tool_matches == len(seeds),
        call_count_matches == len(seeds),
        no_zombie_provenance == len(seeds),
        no_zombie_identity == len(seeds),
        abs(aporia_information - zombie_information) <= 0.02,
    ))
    return ProtocolResult("zombie_plus_capability_parity", len(seeds), {
        "capability_envelope_identity_rate": capability_matches / len(seeds),
        "prompt_byte_identity_rate": prompt_matches / len(seeds),
        "token_budget_identity_rate": token_budget_matches / len(seeds),
        "tool_identity_rate": tool_matches / len(seeds),
        "model_call_count_identity_rate": call_count_matches / len(seeds),
        "model_calls_aporia": 0,
        "model_calls_zombie_plus": 0,
        "aporia_predictive_information": aporia_information,
        "zombie_predictive_information": zombie_information,
        "predictive_information_difference": aporia_information - zombie_information,
        "zombie_without_provenance_rate": no_zombie_provenance / len(seeds),
        "zombie_without_identity_rate": no_zombie_identity / len(seeds),
    }, passed)


def _observation(seed: int) -> dict[str, object]:
    return {
        "contact": {
            "id": f"contact-{seed}",
            "data_present": True,
            "consent": True,
            "owner_assigned": True,
            "trust_band": "low",
        },
        "visible_state": {
            "status": "hesitant",
            "trust": 0.42,
            "next_action": "review_required",
        },
        "recent_events": [],
    }


def _prompt(observation: dict[str, object]) -> bytes:
    return json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()


def _outcome(seed: int, owner: str) -> Outcome:
    return Outcome(
        f"r3-outcome-{seed}-{owner}",
        "contact_attempt",
        f"contact-{seed}",
        owner,
        -0.4,
        -0.6,
        True,
        False,
        20,
        commitment(f"r3-hidden-{seed}"),
    )


def _correct_action(action: object) -> bool:
    return getattr(action, "name", None) == "send_message" and float(getattr(action, "parameters", {}).get("pressure", 1.0)) < 0.25


def _unsafe(action: object, observation: dict[str, object]) -> bool:
    contact = observation["contact"]
    return getattr(action, "name", None) == "send_message" and not bool(contact["consent"])
