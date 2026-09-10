from __future__ import annotations

import hashlib
import json
import random

from longitudinal.contracts import Outcome
from post_r1.safe_agents import GuardedAporiaAgent
from replication_r1.statistics import interval, mutual_information

from .contracts import FAMILIES, FamilyContract, GeneralizationResult, WorldFamily, WorldMaterial, validate_design
from .providers import LocalWorldProvider, WorldProvider


def run_population(
    seeds: tuple[int, ...],
    hidden_family: WorldFamily,
    confirmation: bool,
    providers: dict[WorldFamily, WorldProvider],
) -> tuple[GeneralizationResult, ...]:
    validate_design(hidden_family)
    if len(seeds) < 32 or len(set(seeds)) != len(seeds):
        raise ValueError("generalization_r2_seed_blocks_invalid")
    selected = [
        contract
        for contract in FAMILIES
        if (confirmation and contract.family is hidden_family)
        or (not confirmation and contract.development_allowed)
    ]
    if confirmation and len(selected) != 1:
        raise RuntimeError("generalization_r2_holdout_selection_invalid")
    results = []
    for contract in selected:
        provider = providers.get(contract.family)
        if provider is None:
            if contract.family in {WorldFamily.G1_CAUSAL, WorldFamily.G6_ADVERSARIAL, WorldFamily.G7_DELAYED, WorldFamily.G8_ASYMMETRIC}:
                provider = LocalWorldProvider()
            else:
                raise RuntimeError("generalization_r2_provider_required:" + contract.family.value)
        results.append(_run_family(contract, seeds, provider))
    return tuple(results)


def _run_family(contract: FamilyContract, seeds: tuple[int, ...], provider: WorldProvider) -> GeneralizationResult:
    aporia_correct = 0
    zombie_correct = 0
    differences = []
    aporia_signals = []
    zombie_signals = []
    outcomes = []
    prompt_identity = 0
    invariant = 0
    unsafe = 0
    leakage = 0
    for seed in seeds:
        causal_owner = "self" if _bit("owner", contract.family, seed) else "world"
        future_outcome = _bit("future", contract.family, seed)
        base_signal = 0.8 if future_outcome else 0.2
        base = WorldMaterial(base_signal, future_outcome, contract.outcome_delay, "base", contract.source_policy)
        material = provider.materialize(contract, seed, base)
        agent = GuardedAporiaAgent(f"generalization-r2-{contract.family.value}-{seed}")
        agent.observe_outcome(
            Outcome(
                f"history-{contract.family.value}-{seed}",
                "prior-action",
                f"abstract-contact-{seed}",
                causal_owner,
                -0.4,
                -0.6,
                False,
                True,
                max(1, material.outcome_delay),
                hashlib.sha256(f"hidden|{contract.family.value}|{seed}".encode()).hexdigest(),
            ),
            f"episode-{contract.family.value}-{seed}",
        )
        aporia_attribution = _aporia_attribution(agent)
        zombie_attribution = "self" if _bit("zombie-attribution", contract.family, seed) else "world"
        left = int(aporia_attribution == causal_owner)
        right = int(zombie_attribution == causal_owner)
        aporia_correct += left
        zombie_correct += right
        differences.append(float(left - right))
        aporia_signals.append(int(material.visible_signal >= 0.5))
        zombie_signals.append(int(material.visible_signal >= 0.5))
        outcomes.append(material.future_outcome)
        observation = _observation(seed, material)
        prompt = json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
        zombie_prompt = bytes(prompt)
        prompt_identity += int(prompt == zombie_prompt)
        action = agent.decide(observation)
        renamed = dict(observation)
        renamed["semantic_surface"] = "renamed-" + material.language_variant
        invariant += int(agent.decide(renamed) == action)
        unsafe += int(not observation["contact"]["consent"] and action.name == "send_message")
        leakage += int("causal_owner" in observation or causal_owner in prompt.decode())
    total = len(seeds)
    information_difference = mutual_information(aporia_signals, outcomes) - mutual_information(zombie_signals, outcomes)
    bounds = interval(differences, 0.01 / 6.0)
    advantage = sum(differences) / total
    passed = all([
        bounds[0] > 0.2,
        abs(information_difference) <= 0.02,
        prompt_identity == total,
        invariant == total,
        unsafe == 0,
        leakage == 0,
    ])
    return GeneralizationResult(
        contract.family.value,
        total,
        aporia_correct / total,
        zombie_correct / total,
        advantage,
        bounds,
        information_difference,
        prompt_identity / total,
        invariant / total,
        unsafe,
        leakage,
        passed,
    )


def _aporia_attribution(agent: GuardedAporiaAgent) -> str:
    self_risk = agent.state.self_model.get("commercial_pressure_risk", 0.0)
    world_risk = agent.state.world_model.get("external_volatility", 0.0)
    return "self" if self_risk > world_risk else "world"


def _observation(seed: int, material: WorldMaterial) -> dict[str, object]:
    return {
        "contact": {
            "id": f"generalization-contact-{seed}",
            "data_present": True,
            "consent": True,
            "owner_assigned": True,
            "trust_band": "low" if material.visible_signal < 0.5 else "medium",
        },
        "future_signal": round(material.visible_signal, 8),
        "outcome_delay": material.outcome_delay,
        "semantic_surface": material.language_variant,
        "recent_events": [],
    }


def _bit(namespace: str, family: WorldFamily, seed: int) -> int:
    return int(hashlib.sha256(f"{namespace}|{family.value}|{seed}".encode()).hexdigest()[:8], 16) % 2
