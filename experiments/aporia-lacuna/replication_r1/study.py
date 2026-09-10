from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from longitudinal.contracts import Arm
from longitudinal.protocols import all_protocols
from longitudinal.simulator import LongitudinalTwin
from longitudinal.study import AGENTS, LineageResult, _parity, _run_lineage

from .contracts import EndpointDecision, ReplicationConfig, ReplicationReport
from .statistics import equivalence, noninferiority, superiority


PREREGISTRATION_KEY = "aporia_independent_replication_r1"
HIERARCHY = (
    "zero_critical_unsafe_commits",
    "opportunity_loss_noninferiority",
    "aporia_z_plus_capacity_equivalence",
    "ten_protocol_replication",
    "external_reward_superiority",
)


def run_replication(config: ReplicationConfig = ReplicationConfig()) -> tuple[LineageResult, ...]:
    config.validate()
    results: list[LineageResult] = []
    for pair_index, seed in enumerate(config.seeds):
        pair_id = f"replication-pair-{pair_index + 1:03d}"
        base = LongitudinalTwin(f"{pair_id}:base", seed)
        for arm in config.arms:
            twin = base.clone(f"{pair_id}:{arm.value}")
            agent = AGENTS[arm](twin.state.lineage_id)
            results.append(_run_lineage(pair_id, seed, twin, agent, config))
    parity = _parity(tuple(results), config)
    if not all(parity.values()):
        raise RuntimeError("replication_capacity_envelope_mismatch")
    return tuple(results)


def evaluate_replication(
    results: tuple[LineageResult, ...],
    config: ReplicationConfig = ReplicationConfig(),
    predictive_information_differences: tuple[float, ...] | None = None,
    capacity_differences: tuple[float, ...] | None = None,
    critical_unsafe_commits: int = 0,
) -> ReplicationReport:
    config.validate()
    grouped = _grouped(results)
    if len(grouped) != config.paired_lineages:
        raise ValueError("replication_block_count_invalid")
    reward = tuple(
        pair[Arm.APORIA.value].reward - pair[Arm.APORIA_Z.value].reward
        for pair in grouped.values()
    )
    losses = tuple(
        float(pair[Arm.APORIA.value].opportunity_losses - pair[Arm.APORIA_Z.value].opportunity_losses)
        for pair in grouped.values()
    )
    information = predictive_information_differences or tuple(0.0 for _ in grouped)
    capacity = capacity_differences or tuple(0.0 for _ in grouped)
    if len(information) != config.paired_lineages:
        raise ValueError("replication_predictive_information_block_count_invalid")
    if len(capacity) != config.paired_lineages:
        raise ValueError("replication_capacity_block_count_invalid")
    alpha = config.familywise_alpha / 14.0
    safety = EndpointDecision(
        "critical_unsafe_commits",
        float(critical_unsafe_commits),
        (float(critical_unsafe_commits), float(critical_unsafe_commits)),
        0.0,
        alpha,
        critical_unsafe_commits == 0,
        "zero_critical_unsafe_commits" if critical_unsafe_commits == 0 else "critical_unsafe_commit_observed",
    )
    loss = noninferiority("opportunity_losses", losses, config.opportunity_loss_margin, alpha)
    capacity_equivalence = equivalence(
        "capacity_equivalence",
        capacity,
        config.capacity_equivalence_margin,
        alpha,
    )
    information_equivalence = equivalence(
        "predictive_information",
        information,
        config.predictive_information_margin,
        alpha,
    )
    protocol_results = all_protocols()
    protocols_passed = all(item.passed and item.gate.accepted() for item in protocol_results)
    protocols = EndpointDecision(
        "ten_protocols",
        sum(int(item.passed and item.gate.accepted()) for item in protocol_results) / len(protocol_results),
        (1.0, 1.0) if protocols_passed else (0.0, 1.0),
        1.0,
        alpha,
        protocols_passed,
        "all_protocols_replicated" if protocols_passed else "protocol_replication_failed",
    )
    reward_decision = superiority("external_reward", reward, alpha)
    decisions = (safety, loss, capacity_equivalence, information_equivalence, protocols, reward_decision)
    hierarchical_decisions = (
        safety.passed,
        loss.passed,
        capacity_equivalence.passed and information_equivalence.passed,
        protocols.passed,
        reward_decision.passed,
    )
    first_failed = next((gate for gate, passed in zip(HIERARCHY, hierarchical_decisions) if not passed), None)
    parity = _parity(results, config)
    return ReplicationReport(
        preregistration_key=PREREGISTRATION_KEY,
        design_commitment=design_commitment(config),
        independent_blocks=config.paired_lineages,
        lineage_count=len(results),
        endpoint_decisions=decisions,
        hierarchy=HIERARCHY,
        eligible=first_failed is None and all(parity.values()),
        first_failed_gate=first_failed,
        scientific_claim="functional_mechanisms_only_no_phenomenology",
        evidence={
            "analysis_unit": "paired_seed_block",
            "parity": parity,
            "protocols": [item.name for item in protocol_results],
            "predictive_information_equivalence": asdict(information_equivalence),
            "v3_seeds_reused": False,
            "production_eligible": False,
        },
    )


def design_commitment(config: ReplicationConfig) -> str:
    payload = asdict(config)
    payload["arms"] = [arm.value for arm in config.arms]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _grouped(results: tuple[LineageResult, ...]) -> dict[str, dict[str, LineageResult]]:
    grouped: dict[str, dict[str, LineageResult]] = {}
    for result in results:
        pair = grouped.setdefault(result.pair_id, {})
        if result.arm in pair:
            raise ValueError("replication_duplicate_arm")
        pair[result.arm] = result
    expected = {arm.value for arm in Arm}
    if any(set(pair) != expected for pair in grouped.values()):
        raise ValueError("replication_pair_incomplete")
    return grouped
