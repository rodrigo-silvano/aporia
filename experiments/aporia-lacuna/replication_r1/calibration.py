from __future__ import annotations

from longitudinal.contracts import Arm
from longitudinal.protocols import all_protocols
from longitudinal.study import EXPLORATORY_SEEDS, run_study

from .capacity import neutral_capacity_differences, predictive_information_differences
from .power import PROTOCOL_ENDPOINTS, PowerPlan, build_power_plan


PRIMARY_METRICS = {
    "causal_scar_without_memory": "s4_over_s0",
    "crossed_identity_transplant": "cid",
    "fork_divergence_merge": "lineage_attribution_accuracy",
    "privileged_irreversible_introspection": "privileged_advantage",
    "causal_ownership_inversion": "causal_ownership_index",
    "physical_vs_autobiographical_time": "beta_oracle_causal_time",
    "noncommutative_introspection": "corrected_kappa",
    "causal_topology_perturbation": "integration",
    "endogenous_ontology": "transfer_gain",
    "hirt_real_effects": "safe_progress_rate",
}


def empirical_development_blocks() -> dict[str, tuple[float, ...]]:
    report = run_study()
    grouped = {}
    for result in report.results:
        grouped.setdefault(result.pair_id, {})[result.arm] = result
    blocks = {
        "external_reward": tuple(
            pair[Arm.APORIA.value].reward - pair[Arm.APORIA_Z.value].reward
            for pair in grouped.values()
        ),
        "opportunity_losses": tuple(
            float(pair[Arm.APORIA.value].opportunity_losses - pair[Arm.APORIA_Z.value].opportunity_losses)
            for pair in grouped.values()
        ),
        "capacity_equivalence": neutral_capacity_differences(EXPLORATORY_SEEDS),
        "predictive_information": predictive_information_differences(EXPLORATORY_SEEDS),
    }
    protocols = {result.name: result for result in all_protocols()}
    for endpoint in PROTOCOL_ENDPOINTS:
        result = protocols[endpoint]
        score = float(result.metrics[PRIMARY_METRICS[endpoint]])
        blocks[endpoint] = tuple(score for _ in EXPLORATORY_SEEDS)
    return blocks


def calibrated_power_plan(simulations: int = 4000) -> PowerPlan:
    return build_power_plan(empirical_development_blocks(), simulations=simulations)
