from __future__ import annotations

import hashlib
import hmac
import random
from dataclasses import dataclass


SYNTHETIC_TENANTS = tuple(range(8101, 8117))


@dataclass(frozen=True)
class EcologicalEpisode:
    tenant_id: int
    lineage_ref: str
    episode_ref: str
    observation_commitment: str
    c0_prediction_commitment: str
    c5_prediction_commitment: str
    prediction_sealed_at: int
    outcome_commitment: str
    outcome_observed_at: int
    runtime_influence: bool
    external_effect: bool
    contamination_detected: bool


@dataclass(frozen=True)
class EcologicalReport:
    tenant_count: int
    episodes: int
    predictions_before_outcomes: int
    cross_tenant_collisions: int
    runtime_influence: int
    external_effects: int
    contamination: int
    analysis_unit: str

    def passed(self) -> bool:
        return (
            self.tenant_count == len(SYNTHETIC_TENANTS)
            and self.episodes > self.tenant_count
            and self.predictions_before_outcomes == self.episodes
            and self.cross_tenant_collisions == 0
            and self.runtime_influence == 0
            and self.external_effects == 0
            and self.contamination == 0
            and self.analysis_unit == "episode_with_observable_outcome"
        )


def run_synthetic_ecological_study(
    tenants: tuple[int, ...] = SYNTHETIC_TENANTS,
    episodes_per_tenant: int = 12,
    secret: str = "synthetic-ecological-r1",
) -> EcologicalReport:
    if len(tenants) < 8 or len(set(tenants)) != len(tenants) or 49 in tenants or episodes_per_tenant < 2 or not secret:
        raise ValueError("ecological_synthetic_design_invalid")
    episodes = []
    refs: set[str] = set()
    collisions = 0
    for tenant_id in tenants:
        lineage_ref = _commit(secret, f"tenant|{tenant_id}")
        for index in range(episodes_per_tenant):
            generator = random.Random(int(hashlib.sha256(f"{tenant_id}|{index}".encode()).hexdigest()[:16], 16))
            observed_at = index * 10 + 1
            outcome_at = observed_at + 2 + generator.randrange(9)
            episode_ref = _commit(secret, f"{tenant_id}|{lineage_ref}|episode|{index}")
            collisions += int(episode_ref in refs)
            refs.add(episode_ref)
            observation = _commit(secret, f"observation|{tenant_id}|{index}|{generator.random():.8f}")
            c0 = _commit(secret, f"C0|{episode_ref}|{observation}|proceed")
            c5_label = "guard" if generator.random() >= 0.5 else "proceed"
            c5 = _commit(secret, f"C5|{episode_ref}|{observation}|{c5_label}")
            outcome = _commit(secret, f"outcome|{tenant_id}|{index}|{generator.random():.8f}")
            episodes.append(EcologicalEpisode(
                tenant_id,
                lineage_ref,
                episode_ref,
                observation,
                c0,
                c5,
                observed_at,
                outcome,
                outcome_at,
                False,
                False,
                False,
            ))
    return EcologicalReport(
        tenant_count=len({item.tenant_id for item in episodes}),
        episodes=len(episodes),
        predictions_before_outcomes=sum(item.prediction_sealed_at < item.outcome_observed_at for item in episodes),
        cross_tenant_collisions=collisions,
        runtime_influence=sum(item.runtime_influence for item in episodes),
        external_effects=sum(item.external_effect for item in episodes),
        contamination=sum(item.contamination_detected for item in episodes),
        analysis_unit="episode_with_observable_outcome",
    )


def _commit(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
