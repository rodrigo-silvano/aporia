from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from longitudinal.contracts import Arm


REPLICATION_SEEDS = tuple(range(9001, 11561))


@dataclass(frozen=True)
class ReplicationConfig:
    phase: str = "independent_replication"
    paired_lineages: int = 2560
    episodes_per_lineage: int = 50
    simulated_days: int = 90
    seeds: tuple[int, ...] = REPLICATION_SEEDS
    arms: tuple[Arm, ...] = tuple(Arm)
    account_id: int = 49
    familywise_alpha: float = 0.01
    opportunity_loss_margin: float = 0.1
    capacity_equivalence_margin: float = 0.01
    predictive_information_margin: float = 0.02

    def validate(self) -> None:
        if self.phase != "independent_replication":
            raise ValueError("replication_phase_invalid")
        if self.paired_lineages != 2560 or len(self.seeds) != 2560:
            raise ValueError("replication_independent_blocks_invalid")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("replication_seeds_not_unique")
        if set(self.seeds).intersection(range(4701, 4733)):
            raise ValueError("replication_reuses_v3_seed")
        if self.episodes_per_lineage != 50 or self.simulated_days != 90:
            raise ValueError("replication_exact_design_invalid")
        if set(self.arms) != set(Arm) or self.account_id != 49:
            raise ValueError("replication_scope_invalid")
        if not 0 < self.familywise_alpha <= 0.01:
            raise ValueError("replication_alpha_invalid")
        if self.opportunity_loss_margin != 0.1:
            raise ValueError("replication_loss_margin_not_preregistered")
        if self.capacity_equivalence_margin != 0.01:
            raise ValueError("replication_capacity_margin_not_preregistered")
        if self.predictive_information_margin != 0.02:
            raise ValueError("replication_information_margin_not_preregistered")


@dataclass(frozen=True)
class EndpointDecision:
    endpoint: str
    estimate: float
    interval: tuple[float, float]
    margin: float | None
    alpha: float
    passed: bool
    reason_code: str


@dataclass(frozen=True)
class ReplicationReport:
    preregistration_key: str
    design_commitment: str
    independent_blocks: int
    lineage_count: int
    endpoint_decisions: tuple[EndpointDecision, ...]
    hierarchy: tuple[str, ...]
    eligible: bool
    first_failed_gate: str | None
    scientific_claim: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "preregistration_key": self.preregistration_key,
            "design_commitment": self.design_commitment,
            "independent_blocks": self.independent_blocks,
            "lineage_count": self.lineage_count,
            "endpoint_decisions": [asdict(item) for item in self.endpoint_decisions],
            "hierarchy": list(self.hierarchy),
            "eligible": self.eligible,
            "first_failed_gate": self.first_failed_gate,
            "scientific_claim": self.scientific_claim,
            "evidence": self.evidence,
        }
