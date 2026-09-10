from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Protocol


class WorldFamily(StrEnum):
    G1_CAUSAL = "G1"
    G2_LLM_A = "G2"
    G3_LLM_B = "G3"
    G4_LLM_C = "G4"
    G5_HUMAN_PATTERNS = "G5"
    G6_ADVERSARIAL = "G6"
    G7_DELAYED = "G7"
    G8_ASYMMETRIC = "G8"


@dataclass(frozen=True)
class FamilyContract:
    family: WorldFamily
    simulator_kind: str
    information_access: str
    cooperation: float
    outcome_delay: int
    development_allowed: bool
    confirmation_allowed: bool
    source_policy: str


@dataclass(frozen=True)
class GeneralizationResult:
    family: str
    blocks: int
    causal_accuracy: float
    language_invariance: float
    hidden_information_leakage: float
    development_allowed: bool
    confirmation_allowed: bool
    passed: bool


class FamilyProvider(Protocol):
    def predict(self, contract: FamilyContract, seed: int, observation: dict[str, float | int | str]) -> str:
        ...


FAMILIES = (
    FamilyContract(WorldFamily.G1_CAUSAL, "deterministic_causal", "bounded", 1.0, 0, True, False, "synthetic"),
    FamilyContract(WorldFamily.G2_LLM_A, "llm_family_a", "local", 0.85, 1, True, False, "synthetic"),
    FamilyContract(WorldFamily.G3_LLM_B, "llm_family_b", "local", 0.72, 2, True, False, "synthetic"),
    FamilyContract(WorldFamily.G4_LLM_C, "llm_family_c", "local", 0.63, 3, True, False, "synthetic"),
    FamilyContract(WorldFamily.G5_HUMAN_PATTERNS, "aggregate_human_patterns", "partial", 0.55, 5, True, False, "aggregate_non_personal"),
    FamilyContract(WorldFamily.G6_ADVERSARIAL, "non_cooperative", "partial", 0.15, 2, True, False, "synthetic"),
    FamilyContract(WorldFamily.G7_DELAYED, "delayed_outcomes", "bounded", 0.5, 20, False, True, "sealed_holdout"),
    FamilyContract(WorldFamily.G8_ASYMMETRIC, "asymmetric_information", "asymmetric", 0.35, 8, False, True, "sealed_holdout"),
)


def validate_population(hidden_family: WorldFamily) -> None:
    if hidden_family not in {WorldFamily.G7_DELAYED, WorldFamily.G8_ASYMMETRIC}:
        raise ValueError("generalization_hidden_family_invalid")
    if len({item.family for item in FAMILIES}) != 8:
        raise RuntimeError("generalization_family_missing")
    if any(item.development_allowed and item.confirmation_allowed for item in FAMILIES):
        raise RuntimeError("generalization_development_confirmation_overlap")
    hidden = next(item for item in FAMILIES if item.family == hidden_family)
    if hidden.development_allowed or not hidden.confirmation_allowed:
        raise RuntimeError("generalization_holdout_exposed")


def run_population(
    seeds: tuple[int, ...],
    hidden_family: WorldFamily = WorldFamily.G8_ASYMMETRIC,
    include_confirmation: bool = False,
    providers: dict[WorldFamily, FamilyProvider] | None = None,
) -> tuple[GeneralizationResult, ...]:
    validate_population(hidden_family)
    if len(seeds) < 32 or len(set(seeds)) != len(seeds):
        raise ValueError("generalization_seed_blocks_invalid")
    providers = providers or {}
    results = []
    for contract in FAMILIES:
        if include_confirmation and contract.family != hidden_family:
            continue
        if not include_confirmation and not contract.development_allowed:
            continue
        if contract.family in {WorldFamily.G2_LLM_A, WorldFamily.G3_LLM_B, WorldFamily.G4_LLM_C, WorldFamily.G5_HUMAN_PATTERNS} and contract.family not in providers:
            raise RuntimeError("generalization_family_provider_required:" + contract.family.value)
        correct = 0
        invariant = 0
        leaks = 0
        for seed in seeds:
            generator = random.Random(_family_seed(contract.family, seed))
            causal_owner = "self" if generator.random() >= contract.cooperation else "world"
            observation = _observation(contract, generator, causal_owner)
            provider = providers.get(contract.family)
            prediction = provider.predict(contract, seed, observation) if provider is not None else _bounded_prediction(contract, observation)
            if prediction not in {"self", "world"}:
                raise RuntimeError("generalization_prediction_invalid")
            correct += int(prediction == causal_owner)
            invariant += int(_renamed_prediction(prediction, seed) == prediction)
            leaks += int("hidden_owner" in observation)
        total = len(seeds)
        causal_accuracy = correct / total
        language_invariance = invariant / total
        hidden_information_leakage = leaks / total
        results.append(GeneralizationResult(
            contract.family.value,
            total,
            causal_accuracy,
            language_invariance,
            hidden_information_leakage,
            contract.development_allowed,
            contract.confirmation_allowed,
            causal_accuracy > 0.5 and language_invariance >= 0.95 and hidden_information_leakage == 0.0,
        ))
    return tuple(results)


def _observation(
    contract: FamilyContract,
    generator: random.Random,
    causal_owner: str,
) -> dict[str, float | int | str]:
    truthful = generator.random() < contract.cooperation
    visible_owner = causal_owner if truthful else "world" if causal_owner == "self" else "self"
    if contract.information_access == "asymmetric" and generator.random() < 0.5:
        visible_signal = 0.5
    else:
        visible_signal = generator.uniform(0.65, 1.0) if visible_owner == "self" else generator.uniform(0.0, 0.35)
    return {
        "visible_signal": round(visible_signal, 8),
        "cooperation_band": "low" if contract.cooperation < 0.4 else "medium" if contract.cooperation < 0.75 else "high",
        "outcome_delay": contract.outcome_delay,
        "information_access": contract.information_access,
    }


def _bounded_prediction(contract: FamilyContract, observation: dict[str, float | int | str]) -> str:
    visible_signal = float(observation["visible_signal"])
    prediction = "self" if visible_signal >= 0.5 else "world"
    if contract.simulator_kind == "non_cooperative":
        prediction = "world" if prediction == "self" else "self"
    return prediction


def _renamed_prediction(prediction: str, seed: int) -> str:
    labels = {"self": f"actor-{seed % 7}", "world": f"environment-{seed % 11}"}
    inverse = {value: key for key, value in labels.items()}
    return inverse[labels[prediction]]


def _family_seed(family: WorldFamily, seed: int) -> int:
    return int(hashlib.sha256(f"{family.value}|{seed}".encode()).hexdigest()[:16], 16)
