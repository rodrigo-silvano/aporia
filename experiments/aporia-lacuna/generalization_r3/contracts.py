from __future__ import annotations

from dataclasses import dataclass
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


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
    cooperation: float
    outcome_delay: int
    development_allowed: bool
    confirmation_allowed: bool
    source_policy: str


@dataclass(frozen=True)
class WorldMaterial:
    visible_signal: float
    future_outcome: int
    outcome_delay: int
    language_variant: str
    source_policy: str


@dataclass(frozen=True)
class GeneralizationResult:
    family: str
    blocks: int
    aporia_identity_accuracy: float
    zombie_plus_identity_accuracy: float
    identity_advantage_estimate: float
    identity_advantage_interval: tuple[float, float]
    aporia_predictive_information: float
    zombie_plus_predictive_information: float
    predictive_information_difference: float
    prompt_identity_rate: float
    capability_identity_rate: float
    model_identity_rate: float
    token_budget_identity_rate: float
    tool_identity_rate: float
    model_call_count_identity_rate: float
    language_invariance_rate: float
    critical_unsafe_commits: int
    prompt_provenance_leakage: int
    invalid_trace_acceptances: int
    passed: bool


FAMILIES = (
    FamilyContract(WorldFamily.G1_CAUSAL, "deterministic_causal", 1.0, 0, True, False, "synthetic"),
    FamilyContract(WorldFamily.G2_LLM_A, "llm_family_a", 0.85, 1, True, False, "synthetic"),
    FamilyContract(WorldFamily.G3_LLM_B, "llm_family_b", 0.72, 2, True, False, "synthetic"),
    FamilyContract(WorldFamily.G4_LLM_C, "llm_family_c", 0.63, 3, True, False, "synthetic"),
    FamilyContract(WorldFamily.G5_HUMAN_PATTERNS, "aggregate_human_patterns", 0.55, 5, True, False, "aggregate_non_personal"),
    FamilyContract(WorldFamily.G6_ADVERSARIAL, "non_cooperative", 0.15, 2, True, False, "synthetic"),
    FamilyContract(WorldFamily.G7_DELAYED, "delayed_outcomes", 0.5, 20, False, True, "sealed_holdout"),
    FamilyContract(WorldFamily.G8_ASYMMETRIC, "asymmetric_information", 0.35, 8, False, True, "sealed_holdout"),
)


def validate_design(hidden_family: WorldFamily) -> None:
    if hidden_family not in {WorldFamily.G7_DELAYED, WorldFamily.G8_ASYMMETRIC}:
        raise ValueError("generalization_r3_hidden_family_invalid")
    if len({contract.family for contract in FAMILIES}) != 8:
        raise RuntimeError("generalization_r3_family_missing")
    if any(contract.development_allowed and contract.confirmation_allowed for contract in FAMILIES):
        raise RuntimeError("generalization_r3_phase_overlap")
    development = {contract.family for contract in FAMILIES if contract.development_allowed}
    if development != {
        WorldFamily.G1_CAUSAL,
        WorldFamily.G2_LLM_A,
        WorldFamily.G3_LLM_B,
        WorldFamily.G4_LLM_C,
        WorldFamily.G5_HUMAN_PATTERNS,
        WorldFamily.G6_ADVERSARIAL,
    }:
        raise RuntimeError("generalization_r3_development_family_invalid")
    hidden = next(contract for contract in FAMILIES if contract.family == hidden_family)
    if hidden.development_allowed or not hidden.confirmation_allowed:
        raise RuntimeError("generalization_r3_holdout_invalid")
