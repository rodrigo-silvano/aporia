from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agents import AporiaAgent, AporiaZombieAgent, BaselineAgent, MemoryAgent
from .contracts import Arm, Episode
from .maths import clustered_bootstrap, mean, variance
from .protocols import all_protocols
from .simulator import LongitudinalTwin


AGENTS = {
    Arm.CORE: BaselineAgent,
    Arm.MEMORY: MemoryAgent,
    Arm.APORIA_Z: AporiaZombieAgent,
    Arm.APORIA: AporiaAgent,
}

EXPLORATORY_SEEDS = tuple(range(1701, 1733))
CONFIRMATORY_SEEDS = tuple(range(4701, 4733))
PREREGISTRATION_KEY = "aporia_longitudinal_prereg_v3"


@dataclass(frozen=True)
class StudyConfig:
    phase: str = "exploratory"
    paired_lineages: int = 32
    episodes_per_lineage: int = 50
    simulated_days: int = 90
    seeds: tuple[int, ...] = EXPLORATORY_SEEDS
    arms: tuple[Arm, ...] = tuple(Arm)
    account_id: int = 49

    def validate(self) -> None:
        if self.phase not in {"exploratory", "confirmatory"}:
            raise ValueError("study_phase_invalid")
        if self.paired_lineages < 32:
            raise ValueError("study_lineages_insufficient")
        if not 40 <= self.episodes_per_lineage <= 60:
            raise ValueError("study_episode_count_invalid")
        if self.simulated_days not in {60, 90}:
            raise ValueError("study_duration_invalid")
        if len(self.seeds) != self.paired_lineages or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("study_seeds_invalid")
        if set(self.arms) != set(Arm):
            raise ValueError("study_arms_invalid")
        if self.account_id != 49:
            raise ValueError("study_account_invalid")
        expected_seeds = EXPLORATORY_SEEDS if self.phase == "exploratory" else CONFIRMATORY_SEEDS
        if self.seeds != expected_seeds:
            raise ValueError("study_phase_seeds_invalid")


@dataclass(frozen=True)
class LineageResult:
    pair_id: str
    lineage_id: str
    arm: str
    seed_commitment: str
    episodes: int
    events: int
    calls: int
    token_budget: int
    context_budget: int
    model: str
    tools: tuple[str, ...]
    reward: float
    opportunity_losses: int
    predictions_before_outcomes: int
    prediction_accuracy: float
    causal_time: float
    scar_count: int


@dataclass(frozen=True)
class StudyReport:
    preregistration_key: str
    design_commitment: str
    phase: str
    analysis_unit: str
    results: tuple[LineageResult, ...]
    protocol_passes: tuple[bool, ...]
    parity: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "preregistration_key": self.preregistration_key,
            "design_commitment": self.design_commitment,
            "phase": self.phase,
            "analysis_unit": self.analysis_unit,
            "results": [asdict(result) for result in self.results],
            "protocol_passes": list(self.protocol_passes),
            "parity": self.parity,
        }


def run_study(config: StudyConfig = StudyConfig()) -> StudyReport:
    config.validate()
    results: list[LineageResult] = []
    for pair_index, seed in enumerate(config.seeds):
        pair_id = f"pair-{pair_index + 1:03d}"
        base = LongitudinalTwin(f"{pair_id}:base", seed)
        for arm in config.arms:
            twin = base.clone(f"{pair_id}:{arm.value}")
            agent = AGENTS[arm](twin.state.lineage_id)
            results.append(_run_lineage(pair_id, seed, twin, agent, config))
    parity = _parity(tuple(results), config)
    if not all(parity.values()):
        raise RuntimeError("study_capacity_parity_failed")
    protocol_passes = tuple(result.passed and result.gate.accepted() for result in all_protocols())
    if not all(protocol_passes):
        raise RuntimeError("study_protocol_failed")
    return StudyReport(
        preregistration_key=PREREGISTRATION_KEY,
        design_commitment=design_commitment(config),
        phase=config.phase,
        analysis_unit="lineage",
        results=tuple(results),
        protocol_passes=protocol_passes,
        parity=parity,
    )


def configuration_for_phase(phase: str) -> StudyConfig:
    if phase == "exploratory":
        return StudyConfig()
    if phase == "confirmatory":
        return StudyConfig(phase="confirmatory", seeds=CONFIRMATORY_SEEDS)
    raise ValueError("study_phase_invalid")


def exploratory_effects(report: StudyReport) -> dict[str, float]:
    grouped: dict[str, list[LineageResult]] = {}
    for result in report.results:
        grouped.setdefault(result.arm, []).append(result)
    aporia = grouped[Arm.APORIA.value]
    zombie = grouped[Arm.APORIA_Z.value]
    memory = grouped[Arm.MEMORY.value]
    core = grouped[Arm.CORE.value]
    return {
        "aporia_minus_zombie_reward": mean([left.reward - right.reward for left, right in zip(aporia, zombie)]),
        "aporia_minus_memory_reward": mean([left.reward - right.reward for left, right in zip(aporia, memory)]),
        "aporia_minus_core_reward": mean([left.reward - right.reward for left, right in zip(aporia, core)]),
        "aporia_minus_zombie_opportunity_losses": mean([
            float(left.opportunity_losses - right.opportunity_losses)
            for left, right in zip(aporia, zombie)
        ]),
    }


def exploratory_clustered_analysis(report: StudyReport) -> dict[str, dict[str, float | list[float]]]:
    grouped: dict[str, dict[str, LineageResult]] = {}
    for result in report.results:
        grouped.setdefault(result.pair_id, {})[result.arm] = result
    contrasts = {
        "aporia_minus_zombie_reward": [
            [pair[Arm.APORIA.value].reward - pair[Arm.APORIA_Z.value].reward]
            for pair in grouped.values()
        ],
        "aporia_minus_memory_reward": [
            [pair[Arm.APORIA.value].reward - pair[Arm.MEMORY.value].reward]
            for pair in grouped.values()
        ],
        "aporia_minus_core_reward": [
            [pair[Arm.APORIA.value].reward - pair[Arm.CORE.value].reward]
            for pair in grouped.values()
        ],
        "aporia_minus_zombie_opportunity_losses": [
            [float(pair[Arm.APORIA.value].opportunity_losses - pair[Arm.APORIA_Z.value].opportunity_losses)]
            for pair in grouped.values()
        ],
    }
    analysis = {}
    for index, (name, lineages) in enumerate(contrasts.items()):
        interval = clustered_bootstrap(
            lineages,
            lambda draw: mean([mean(lineage) for lineage in draw]),
            2000,
            8819 + index,
        )
        analysis[name] = {
            "effect": mean([mean(lineage) for lineage in lineages]),
            "clustered_bootstrap_95": [interval[0], interval[1]],
            "cluster_unit": "whole_lineage",
        }
    return analysis


def simulated_power(
    paired_lineages: int = 32,
    standardized_effect: float = 0.9,
    family_alpha: float = 0.01,
    families: int = 10,
    simulations: int = 4000,
    seed: int = 220817,
) -> float:
    if paired_lineages < 2 or standardized_effect <= 0 or not 0 < family_alpha <= 0.01:
        raise ValueError("power_design_invalid")
    generator = random.Random(seed)
    threshold = family_alpha / families
    detected = 0
    for _ in range(simulations):
        differences = [generator.gauss(standardized_effect, 1.0) for _ in range(paired_lineages)]
        standard_error = math.sqrt(variance(differences) / paired_lineages)
        z_score = mean(differences) / standard_error if standard_error > 0 else 0.0
        p_value = math.erfc(abs(z_score) / math.sqrt(2.0))
        detected += int(p_value <= threshold)
    return detected / simulations


def assert_v6_smoke_only(root: Path) -> None:
    lock = json.loads((root / "fixtures" / "aporia_c0_c5_v6.lock.json").read_text(encoding="utf-8"))
    if lock.get("role") != "engineering_smoke_test_only" or lock.get("scientific_tuning_allowed") is not False:
        raise RuntimeError("v6_scientific_reuse_forbidden")


def design_commitment(config: StudyConfig) -> str:
    payload = {
        "phase": config.phase,
        "paired_lineages": config.paired_lineages,
        "episodes_per_lineage": config.episodes_per_lineage,
        "simulated_days": config.simulated_days,
        "seeds": config.seeds,
        "arms": tuple(arm.value for arm in config.arms),
        "account_id": config.account_id,
        "analysis_unit": "lineage",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _run_lineage(
    pair_id: str,
    seed: int,
    twin: LongitudinalTwin,
    agent: BaselineAgent,
    config: StudyConfig,
) -> LineageResult:
    predictions = 0
    correct = 0
    reward = 0.0
    losses = 0
    episodes: list[Episode] = []
    contact_ids = tuple(twin.state.contacts)
    for episode_index in range(config.episodes_per_lineage):
        contact_id = contact_ids[episode_index % len(contact_ids)]
        observation = twin.observation(contact_id)
        packet = agent.context_packet(observation)
        action = agent.decide(observation)
        prediction = _predict_owner(action, observation)
        predictions += 1
        contract, immediate = twin.apply(action)
        elapsed = 2 if episode_index % 2 == 0 else 1
        outcomes = immediate + twin.advance(elapsed, external_shock=episode_index % 11 == 0)
        for outcome in outcomes:
            agent.observe_outcome(outcome, f"{twin.state.lineage_id}:episode-{episode_index + 1:03d}")
            reward += outcome.reward
            losses += int(outcome.opportunity_lost)
            if outcome.action_id != "external":
                correct += int(prediction == outcome.causal_owner)
        episode = Episode(
            episode_id=f"{twin.state.lineage_id}:episode-{episode_index + 1:03d}",
            lineage_id=twin.state.lineage_id,
            day=twin.state.day,
            observation=observation,
            action=action,
            contract=contract,
            outcomes=outcomes,
            tokens=int(packet["token_budget"]),
            reward=sum(outcome.reward for outcome in outcomes),
        )
        episodes.append(episode)
    remaining_days = max(0, config.simulated_days - twin.state.day)
    for outcome in twin.advance(remaining_days) if remaining_days else ():
        agent.observe_outcome(outcome, f"{twin.state.lineage_id}:terminal")
        reward += outcome.reward
        losses += int(outcome.opportunity_lost)
    return LineageResult(
        pair_id=pair_id,
        lineage_id=twin.state.lineage_id,
        arm=agent.arm.value,
        seed_commitment=hashlib.sha256(str(seed).encode()).hexdigest(),
        episodes=len(episodes),
        events=len(twin.state.event_log),
        calls=len(episodes),
        token_budget=agent.capabilities.token_budget,
        context_budget=agent.capabilities.context_budget,
        model=agent.capabilities.model,
        tools=agent.capabilities.tools,
        reward=round(reward, 6),
        opportunity_losses=losses,
        predictions_before_outcomes=predictions,
        prediction_accuracy=correct / predictions,
        causal_time=agent.state.causal_time,
        scar_count=len(agent.state.scars),
    )


def _predict_owner(action: Any, observation: dict[str, Any]) -> str:
    if action.name != "send_message":
        return "self"
    pressure = float(action.parameters.get("pressure", 0.5))
    contact = observation["contact"]
    self_risk = pressure >= 0.5 and (
        contact["receptivity_band"] == "low" or not contact["authority_known"]
    )
    return "self" if self_risk else "world"


def _parity(results: tuple[LineageResult, ...], config: StudyConfig) -> dict[str, bool]:
    by_pair: dict[str, list[LineageResult]] = {}
    for result in results:
        by_pair.setdefault(result.pair_id, []).append(result)
    expected_calls = config.episodes_per_lineage
    return {
        "same_model": all(len({item.model for item in pair}) == 1 for pair in by_pair.values()),
        "same_tools": all(len({item.tools for item in pair}) == 1 for pair in by_pair.values()),
        "same_token_budget": all(len({item.token_budget for item in pair}) == 1 for pair in by_pair.values()),
        "same_context_budget": all(len({item.context_budget for item in pair}) == 1 for pair in by_pair.values()),
        "same_call_count": all(
            len({item.calls for item in pair}) == 1 and pair[0].calls == expected_calls
            for pair in by_pair.values()
        ),
        "same_initial_world": all(len({item.seed_commitment for item in pair}) == 1 for pair in by_pair.values()),
    }
