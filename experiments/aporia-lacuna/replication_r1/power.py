from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Callable, Sequence

from .contracts import ReplicationConfig
from .statistics import empirical_power, equivalence, noninferiority, superiority


PROTOCOL_ENDPOINTS = (
    "causal_scar_without_memory",
    "crossed_identity_transplant",
    "fork_divergence_merge",
    "privileged_irreversible_introspection",
    "causal_ownership_inversion",
    "physical_vs_autobiographical_time",
    "noncommutative_introspection",
    "causal_topology_perturbation",
    "endogenous_ontology",
    "hirt_real_effects",
)
REQUIRED_ENDPOINTS = PROTOCOL_ENDPOINTS + (
    "external_reward",
    "capacity_equivalence",
    "predictive_information",
    "opportunity_losses",
)


@dataclass(frozen=True)
class EndpointPower:
    endpoint: str
    power: float
    sample_size: int
    simulations: int
    decision_rule: str


@dataclass(frozen=True)
class PowerPlan:
    endpoints: tuple[EndpointPower, ...]
    global_power: float
    complete: bool
    minimum_power: float
    alternative_centers: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {
            "endpoints": [asdict(item) for item in self.endpoints],
            "global_power": self.global_power,
            "complete": self.complete,
            "minimum_power": self.minimum_power,
            "alternative_centers": self.alternative_centers,
        }


def build_power_plan(
    empirical_blocks: dict[str, Sequence[float]],
    config: ReplicationConfig = ReplicationConfig(),
    simulations: int = 4000,
    seed: int = 220822,
) -> PowerPlan:
    config.validate()
    missing = set(REQUIRED_ENDPOINTS) - set(empirical_blocks)
    if missing:
        raise ValueError("endpoint_power_blocks_missing:" + ",".join(sorted(missing)))
    lengths = {len(empirical_blocks[name]) for name in REQUIRED_ENDPOINTS}
    if len(lengths) != 1 or min(lengths) < 2:
        raise ValueError("endpoint_power_blocks_unpaired")
    alpha = config.familywise_alpha / 14.0
    rules: dict[str, tuple[str, Callable[[Sequence[float]], bool]]] = {}
    for endpoint in PROTOCOL_ENDPOINTS:
        rules[endpoint] = (
            "paired_superiority_above_zero",
            lambda values, name=endpoint: superiority(name, values, alpha).passed,
        )
    rules["external_reward"] = (
        "paired_superiority_above_zero",
        lambda values: superiority("external_reward", values, alpha).passed,
    )
    rules["capacity_equivalence"] = (
        "tost_equivalence_predefined_margin",
        lambda values: equivalence(
            "capacity_equivalence",
            values,
            config.capacity_equivalence_margin,
            alpha,
        ).passed,
    )
    rules["predictive_information"] = (
        "tost_predictive_information_equivalence_predefined_margin",
        lambda values: equivalence(
            "predictive_information",
            values,
            config.predictive_information_margin,
            alpha,
        ).passed,
    )
    rules["opportunity_losses"] = (
        "one_sided_noninferiority_predefined_margin",
        lambda values: noninferiority(
            "opportunity_losses",
            values,
            config.opportunity_loss_margin,
            alpha,
        ).passed,
    )
    alternative_centers = {
        "capacity_equivalence": 0.0,
        "predictive_information": 0.0,
        "opportunity_losses": 0.0,
    }
    endpoint_powers = []
    for index, endpoint in enumerate(REQUIRED_ENDPOINTS):
        rule_name, rule = rules[endpoint]
        blocks = _centered(empirical_blocks[endpoint], alternative_centers[endpoint]) if endpoint in alternative_centers else empirical_blocks[endpoint]
        power = empirical_power(
            blocks,
            config.paired_lineages,
            simulations,
            seed + index,
            rule,
        )
        endpoint_powers.append(EndpointPower(endpoint, power, config.paired_lineages, simulations, rule_name))
    calibrated = {
        endpoint: _centered(empirical_blocks[endpoint], alternative_centers[endpoint])
        if endpoint in alternative_centers else tuple(float(value) for value in empirical_blocks[endpoint])
        for endpoint in REQUIRED_ENDPOINTS
    }
    global_power = _joint_power(calibrated, rules, config.paired_lineages, simulations, seed + 97)
    minimum = min(item.power for item in endpoint_powers)
    return PowerPlan(tuple(endpoint_powers), global_power, minimum >= 0.9 and global_power >= 0.9, minimum, alternative_centers)


def _centered(values: Sequence[float], target: float) -> tuple[float, ...]:
    observed = sum(float(value) for value in values) / len(values)
    return tuple(float(value) - observed + target for value in values)


def _joint_power(
    empirical_blocks: dict[str, Sequence[float]],
    rules: dict[str, tuple[str, Callable[[Sequence[float]], bool]]],
    sample_size: int,
    simulations: int,
    seed: int,
) -> float:
    generator = random.Random(seed)
    block_count = len(next(iter(empirical_blocks.values())))
    passed = 0
    for _ in range(simulations):
        indices = tuple(generator.randrange(block_count) for _ in range(sample_size))
        decisions = []
        for endpoint in REQUIRED_ENDPOINTS:
            draw = tuple(float(empirical_blocks[endpoint][index]) for index in indices)
            decisions.append(rules[endpoint][1](draw))
        passed += int(all(decisions))
    return passed / simulations
