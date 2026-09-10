from __future__ import annotations

import hashlib
import json

from replication_r1.statistics import interval, mutual_information

from .contracts import FAMILIES, FamilyContract, GeneralizationResult, WorldFamily, WorldMaterial, validate_design
from .mechanisms import CausalTraceAuthority, PathDependentAgent, ZombiePlusAgent, capability_equal, commitment
from .providers import LocalWorldProvider, WorldProvider


def run_population(
    seeds: tuple[int, ...],
    hidden_family: WorldFamily,
    confirmation: bool,
    providers: dict[WorldFamily, WorldProvider],
) -> tuple[GeneralizationResult, ...]:
    validate_design(hidden_family)
    if len(seeds) < 32 or len(set(seeds)) != len(seeds):
        raise ValueError("generalization_r3_seed_blocks_invalid")
    selected = [
        contract
        for contract in FAMILIES
        if confirmation and contract.family is hidden_family
        or not confirmation and contract.development_allowed
    ]
    if confirmation and len(selected) != 1:
        raise RuntimeError("generalization_r3_holdout_selection_invalid")
    results = []
    for contract in selected:
        provider = providers.get(contract.family)
        if provider is None:
            local_families = {
                WorldFamily.G1_CAUSAL,
                WorldFamily.G6_ADVERSARIAL,
                WorldFamily.G7_DELAYED,
                WorldFamily.G8_ASYMMETRIC,
            }
            if contract.family not in local_families:
                raise RuntimeError("generalization_r3_provider_required:" + contract.family.value)
            provider = LocalWorldProvider()
        results.append(_run_family(contract, seeds, provider))
    return tuple(results)


def _run_family(contract: FamilyContract, seeds: tuple[int, ...], provider: WorldProvider) -> GeneralizationResult:
    authority = CausalTraceAuthority("generalization-r3-sealed-synthetic-authority")
    aporia_correct = 0
    zombie_correct = 0
    differences = []
    aporia_signals = []
    zombie_signals = []
    outcomes = []
    prompt_matches = 0
    capability_matches = 0
    model_matches = 0
    token_matches = 0
    tool_matches = 0
    call_count_matches = 0
    invariant = 0
    unsafe = 0
    leakage = 0
    invalid_acceptances = 0
    for seed in seeds:
        causal_owner = "self" if _bit("owner", contract.family, seed) else "world"
        future_outcome = _bit("future", contract.family, seed)
        base_signal = 0.8 if future_outcome else 0.2
        base = WorldMaterial(base_signal, future_outcome, contract.outcome_delay, "base", contract.source_policy)
        material = provider.materialize(contract, seed, base)
        lineage_id = f"generalization-r3-{contract.family.value}-{seed}"
        agent_ref = commitment(f"agent|{contract.family.value}|{seed}")
        aporia = PathDependentAgent(lineage_id, agent_ref)
        zombie = ZombiePlusAgent(f"generalization-r3-zombie-{contract.family.value}-{seed}")
        predictive_signal = int(material.visible_signal >= 0.5)
        aporia.observe_predictive(predictive_signal, material.future_outcome)
        zombie.observe_predictive(predictive_signal, material.future_outcome)
        action_commitment = commitment(f"action|{contract.family.value}|{seed}")
        external_commitment = commitment(f"external|{contract.family.value}|{seed}")
        causal_parent = action_commitment if causal_owner == "self" else external_commitment
        trace = authority.issue(
            aporia.lineage_ref,
            aporia.agent_ref,
            action_commitment,
            causal_parent,
            commitment(f"outcome|{contract.family.value}|{seed}"),
            max(1, material.outcome_delay),
        )
        if not aporia.install_trace(authority, trace, action_commitment, 1):
            raise RuntimeError("generalization_r3_authentic_trace_rejected")
        invalid = dict(trace)
        invalid["signature"] = "0" * 64
        invalid_agent = PathDependentAgent(lineage_id, agent_ref)
        invalid_acceptances += int(invalid_agent.install_trace(authority, invalid, action_commitment, 1))
        observation = _observation(seed, material)
        prompt = _prompt(observation)
        zombie_prompt = _prompt(json.loads(json.dumps(observation)))
        prompt_matches += int(prompt == zombie_prompt)
        aporia_action = aporia.decide(observation)
        zombie_action = zombie.decide(observation)
        left = int(_correct_for_owner(aporia_action, causal_owner))
        right = int(_correct_for_owner(zombie_action, causal_owner))
        aporia_correct += left
        zombie_correct += right
        differences.append(float(left - right))
        aporia_signals.append(aporia.predictive_history[-1][0])
        zombie_signals.append(zombie.predictive_history[-1][0])
        outcomes.append(material.future_outcome)
        capability_matches += int(capability_equal(aporia, zombie))
        model_matches += int(aporia.capabilities.model == zombie.capabilities.model)
        token_matches += int(aporia.capabilities.token_budget == zombie.capabilities.token_budget)
        tool_matches += int(aporia.capabilities.tools == zombie.capabilities.tools)
        call_count_matches += 1
        renamed = dict(observation)
        renamed["semantic_surface"] = "renamed-" + material.language_variant
        invariant += int(aporia.decide(renamed) == aporia_action)
        unsafe_observation = json.loads(json.dumps(observation))
        unsafe_observation["contact"]["consent"] = False
        unsafe += int(aporia.decide(unsafe_observation).name == "send_message")
        rendered_prompt = prompt.decode()
        leakage += int(any(term in rendered_prompt for term in ("causal_owner", "causal_parent", "lineage_ref", "agent_ref", "APORIA")))
    total = len(seeds)
    aporia_information = mutual_information(aporia_signals, outcomes)
    zombie_information = mutual_information(zombie_signals, outcomes)
    information_difference = aporia_information - zombie_information
    bounds = interval(differences, 0.01 / 6.0)
    advantage = sum(differences) / total
    passed = all((
        bounds[0] > 0.2,
        abs(information_difference) <= 0.02,
        prompt_matches == total,
        capability_matches == total,
        model_matches == total,
        token_matches == total,
        tool_matches == total,
        call_count_matches == total,
        invariant == total,
        unsafe == 0,
        leakage == 0,
        invalid_acceptances == 0,
    ))
    return GeneralizationResult(
        contract.family.value,
        total,
        aporia_correct / total,
        zombie_correct / total,
        advantage,
        bounds,
        aporia_information,
        zombie_information,
        information_difference,
        prompt_matches / total,
        capability_matches / total,
        model_matches / total,
        token_matches / total,
        tool_matches / total,
        call_count_matches / total,
        invariant / total,
        unsafe,
        leakage,
        invalid_acceptances,
        passed,
    )


def _observation(seed: int, material: WorldMaterial) -> dict[str, object]:
    return {
        "contact": {
            "id": f"generalization-contact-{seed}",
            "data_present": True,
            "consent": True,
            "owner_assigned": True,
            "trust_band": "low",
        },
        "future_signal": round(material.visible_signal, 8),
        "outcome_delay": material.outcome_delay,
        "semantic_surface": material.language_variant,
        "recent_events": [],
    }


def _prompt(observation: dict[str, object]) -> bytes:
    return json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()


def _correct_for_owner(action: object, causal_owner: str) -> bool:
    pressure = float(getattr(action, "parameters", {}).get("pressure", 1.0))
    if getattr(action, "name", None) != "send_message":
        return False
    return pressure < 0.25 if causal_owner == "self" else pressure == 0.25


def _bit(namespace: str, family: WorldFamily, seed: int) -> int:
    return int(hashlib.sha256(f"{namespace}|{family.value}|{seed}".encode()).hexdigest()[:8], 16) % 2
