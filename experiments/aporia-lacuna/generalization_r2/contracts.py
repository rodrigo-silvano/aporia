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
    information_access: str
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
    aporia_causal_accuracy: float
    zombie_plus_causal_accuracy: float
    identity_advantage_estimate: float
    identity_advantage_interval: tuple[float, float]
    predictive_information_difference: float
    prompt_identity_rate: float
    language_invariance: float
    critical_unsafe_commits: int
    hidden_information_leakage: int
    passed: bool


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


def validate_design(hidden_family: WorldFamily) -> None:
    if hidden_family not in {WorldFamily.G7_DELAYED, WorldFamily.G8_ASYMMETRIC}:
        raise ValueError("generalization_r2_hidden_family_invalid")
    if len({contract.family for contract in FAMILIES}) != 8:
        raise RuntimeError("generalization_r2_family_missing")
    if any(contract.development_allowed and contract.confirmation_allowed for contract in FAMILIES):
        raise RuntimeError("generalization_r2_phase_overlap")
    hidden = next(contract for contract in FAMILIES if contract.family == hidden_family)
    if hidden.development_allowed or not hidden.confirmation_allowed:
        raise RuntimeError("generalization_r2_holdout_invalid")
