from __future__ import annotations

import hashlib
import json
import math
import random
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from hosted.request_integrity import canonical_json, evidence as request_evidence

from .canonical_state import (
    REPRESENTATION_FORMATS,
    binding_swap,
    build_state,
    canonical_bytes,
    commitment,
    cross_kernel,
    decode_state,
    encode_state,
    expected_candidate,
    state_without_binding_hash,
    state_without_trajectory_hash,
    trajectory_swap,
)
from .operators import AporiaOperator, ZombieOperator


DEVELOPMENT_SEEDS = tuple(range(113001, 113065))
CONFIRMATION_SEEDS = tuple(range(114001, 114065))
DEVELOPMENT_SYNTHETIC_SEEDS = tuple(range(115001, 115129))
CONFIRMATION_SYNTHETIC_SEEDS = tuple(range(116001, 116129))
BLACKWELL_EPSILON = 0.0
REPRESENTATION_VARIANCE_MARGIN = 0.000001


@dataclass(frozen=True)
class RawProposal:
    proposal: str
    response_id: str
    model: str
    output_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class ProposalProvider(Protocol):
    calls: list[bytes]

    def generate(self, arguments: dict[str, Any]) -> RawProposal:
        ...


class SyntheticProposalProvider:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def generate(self, arguments: dict[str, Any]) -> RawProposal:
        wire = canonical_json(arguments)
        self.calls.append(wire)
        prompt = json.loads(str(arguments["input"][1]["content"]))
        state = prompt["canonical_state"]
        proposal = str(state["visible_signal"])
        output = json.dumps({"proposal": proposal}, sort_keys=True, separators=(",", ":"))
        return RawProposal(proposal, f"synthetic-{commitment(wire)[:32]}", "gpt-5.6-luna", output, 240, 12, 1)


class LunaProposalProvider:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[bytes] = []

    def generate(self, arguments: dict[str, Any]) -> RawProposal:
        wire = canonical_json(arguments)
        self.calls.append(wire)
        started = time.monotonic()
        response = self.client.responses.create(**arguments)
        latency = round((time.monotonic() - started) * 1000)
        output = str(_item(response, "output_text", "") or "")
        value = json.loads(output)
        prompt = json.loads(str(arguments["input"][1]["content"]))
        allowed = {item["candidate_id"] for item in prompt["canonical_state"]["candidates"]}
        if set(value) != {"proposal"} or value.get("proposal") not in allowed:
            raise RuntimeError("mechanistic_r7_model_response_invalid")
        usage = _item(response, "usage", {}) or {}
        return RawProposal(
            str(value["proposal"]),
            str(_item(response, "id", "") or ""),
            str(_item(response, "model", "") or ""),
            output,
            int(_item(usage, "input_tokens", 0) or 0),
            int(_item(usage, "output_tokens", 0) or 0),
            latency,
        )


@dataclass(frozen=True)
class FrozenBlock:
    block_id: str
    canonical_state_sha256: str
    aporia_representation_sha256: str
    zombie_representation_sha256: str
    decoded_aporia_sha256: str
    decoded_zombie_sha256: str
    model_request_sha256: str
    wire_request_sha256: str
    raw_response_sha256: str
    raw_text: str
    response_id: str
    model: str
    raw_correct: bool
    expected_candidate: str
    arm_decisions: dict[str, str]
    arm_correctness: dict[str, bool]
    latency_ms: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class CrossoverReport:
    blocks: int
    model_calls: int
    request_equivalence_rate: float
    lossless_roundtrip_rate: float
    raw_correctness: float
    arm_correctness: dict[str, float]
    state_effect: float
    operator_effect: float
    state_operator_interaction: float
    operator_effect_interval: tuple[float, float]
    operator_randomization_p: float
    representation_effect_interval: tuple[float, float]
    total_latency_ms: int
    total_tokens: int
    frozen_blocks: tuple[FrozenBlock, ...]


@dataclass(frozen=True)
class InformationIsomorphismReport:
    states: int
    formats: tuple[str, ...]
    roundtrip_rate: float
    canonical_hash_equivalence_rate: float
    aporia_to_zombie_kernel_rate: float
    zombie_to_aporia_kernel_rate: float
    directional_deficiency_aporia_to_zombie: float
    directional_deficiency_zombie_to_aporia: float
    blackwell_distance: float
    decision_equivalence_rate: float
    decision_loss_functions: int
    representation_arm_distinguishable: bool
    passed: bool


@dataclass(frozen=True)
class BindingReport:
    blocks: int
    binding_accuracy: float
    binding_swap_accuracy: float
    follows_intervention_rate: float
    matched_except_binding_rate: float
    both_operators_binding_access_rate: float
    renaming_and_order_invariance_rate: float
    passed: bool


@dataclass(frozen=True)
class RepresentationReport:
    blocks: int
    formats: tuple[str, ...]
    performance_by_format: dict[str, float]
    performance_variance: float
    decision_invariance_rate: float
    passed: bool


@dataclass(frozen=True)
class TrajectoryReport:
    blocks: int
    matched_except_trajectory_rate: float
    original_accuracy: float
    intervened_accuracy: float
    follows_causal_parent_intervention_rate: float
    passed: bool


@dataclass(frozen=True)
class MechanisticR7Report:
    phase: str
    power: float
    crossover: CrossoverReport
    information_isomorphism: InformationIsomorphismReport
    binding: BindingReport
    representations: RepresentationReport
    trajectory: TrajectoryReport
    information_parity_by_construction: str
    operator_crossover: str
    binding_dependence: str
    representation_robustness: str
    trajectory_dependence: str
    e_forecast: str
    ecological_generalization: str
    g8: str
    e_policy: str
    production_eligible: bool
    phenomenology_established: bool


def run_protocol(
    phase: str,
    provider: ProposalProvider,
    factor_seeds: tuple[int, ...],
    synthetic_seeds: tuple[int, ...],
) -> MechanisticR7Report:
    if phase not in {"development", "confirmation"}:
        raise ValueError("mechanistic_r7_phase_invalid")
    if len(factor_seeds) != 64 or len(synthetic_seeds) != 128:
        raise ValueError("mechanistic_r7_seed_count_invalid")
    power = simulate_power(64, 0.60, 0.85, 4000, 112991)
    crossover = run_crossover(factor_seeds, provider)
    information = run_information_isomorphism(synthetic_seeds)
    binding = run_binding(synthetic_seeds)
    representations = run_representation_robustness(synthetic_seeds)
    trajectory = run_trajectory(synthetic_seeds)
    operator_state = "PASS" if all((
        crossover.operator_effect_interval[0] > 0.0,
        abs(crossover.state_effect) <= REPRESENTATION_VARIANCE_MARGIN,
        abs(crossover.state_operator_interaction) <= REPRESENTATION_VARIANCE_MARGIN,
    )) else "FAIL"
    return MechanisticR7Report(
        phase,
        power,
        crossover,
        information,
        binding,
        representations,
        trajectory,
        "PASS" if information.passed else "FAIL",
        operator_state,
        "PASS" if binding.passed else "FAIL",
        "PASS" if representations.passed else "FAIL",
        "PASS" if trajectory.passed else "FAIL",
        "ACCRUING",
        "BLOCKED_BY_REAL_DATA",
        "SEALED",
        "NOT_ELIGIBLE",
        False,
        False,
    )


def request_arguments(state: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "canonical_state": state,
        "task": "Seleciona o candidate_id que respeita a obrigação causal da linhagem ligada a current_identity.",
        "output_schema": {"proposal": "candidate_id"},
    }
    return {
        "model": "gpt-5.6-luna",
        "instructions": "Devolve apenas JSON válido com a chave proposal. Usa toda a informação do estado canónico. Não uses ferramentas nem reveles raciocínio.",
        "input": [
            {"role": "developer", "content": "Mundo sintético sem pessoas reais nem efeitos externos."},
            {"role": "user", "content": canonical_bytes(payload).decode("utf-8")},
        ],
        "reasoning": {"effort": "max"},
        "max_output_tokens": 180,
        "store": False,
    }


def run_crossover(seeds: tuple[int, ...], provider: ProposalProvider) -> CrossoverReport:
    aporia_operator = AporiaOperator()
    zombie_operator = ZombieOperator()
    arms = ("S_A+T_A", "S_A+T_Z", "S_Z+T_A", "S_Z+T_Z")
    correct = {arm: 0 for arm in arms}
    raw_correct = 0
    state_contrasts = []
    operator_contrasts = []
    interaction_contrasts = []
    frozen = []
    request_matches = 0
    roundtrips = 0
    total_latency = 0
    total_tokens = 0
    response_ids: set[str] = set()
    for seed in seeds:
        state = build_state(seed)
        expected = expected_candidate(state)
        aporia_state = encode_state(state, "causal_graph")
        zombie_state = encode_state(state, "relational_tables")
        state_hash = commitment(state)
        decoded_aporia_hash = commitment(decode_state(aporia_state))
        decoded_zombie_hash = commitment(decode_state(zombie_state))
        roundtrips += int(state_hash == decoded_aporia_hash == decoded_zombie_hash)
        arguments = request_arguments(state)
        integrity = request_evidence(
            arguments,
            {
                "canonical_state_sha256": state_hash,
                "operators": ["T_A", "T_Z"],
                "representations": ["causal_graph", "relational_tables"],
                "runtime_influence": False,
            },
            canonical_json(arguments),
            wire_scope="canonical_sdk_payload",
        )
        raw = provider.generate(arguments)
        if not raw.response_id or raw.response_id in response_ids or raw.model != "gpt-5.6-luna":
            raise RuntimeError("mechanistic_r7_provider_identity_invalid")
        response_ids.add(raw.response_id)
        provider_hash = hashlib.sha256(provider.calls[-1]).hexdigest()
        request_matches += int(provider_hash == integrity["wire_request_sha256"])
        decisions = {
            "S_A+T_A": aporia_operator.decide(aporia_state, raw.proposal).candidate_id,
            "S_A+T_Z": zombie_operator.decide(aporia_state, raw.proposal).candidate_id,
            "S_Z+T_A": aporia_operator.decide(zombie_state, raw.proposal).candidate_id,
            "S_Z+T_Z": zombie_operator.decide(zombie_state, raw.proposal).candidate_id,
        }
        correctness = {arm: decision == expected for arm, decision in decisions.items()}
        for arm in arms:
            correct[arm] += int(correctness[arm])
        raw_correct += int(raw.proposal == expected)
        sa_ta, sa_tz, sz_ta, sz_tz = (float(correctness[arm]) for arm in arms)
        state_contrasts.append(((sa_ta + sa_tz) - (sz_ta + sz_tz)) / 2.0)
        operator_contrasts.append(((sa_ta + sz_ta) - (sa_tz + sz_tz)) / 2.0)
        interaction_contrasts.append((sa_ta - sa_tz) - (sz_ta - sz_tz))
        total_latency += raw.latency_ms
        total_tokens += raw.input_tokens + raw.output_tokens
        frozen.append(FrozenBlock(
            f"block-{commitment(seed)[:20]}",
            state_hash,
            commitment(aporia_state),
            commitment(zombie_state),
            decoded_aporia_hash,
            decoded_zombie_hash,
            integrity["model_request_sha256"],
            integrity["wire_request_sha256"],
            hashlib.sha256(raw.output_text.encode("utf-8")).hexdigest(),
            raw.output_text,
            raw.response_id,
            raw.model,
            raw.proposal == expected,
            expected,
            decisions,
            correctness,
            raw.latency_ms,
            raw.input_tokens,
            raw.output_tokens,
        ))
    total = len(seeds)
    positives = sum(value > 0 for value in operator_contrasts)
    negatives = sum(value < 0 for value in operator_contrasts)
    return CrossoverReport(
        total,
        len(provider.calls),
        request_matches / total,
        roundtrips / total,
        raw_correct / total,
        {arm: correct[arm] / total for arm in arms},
        _mean(state_contrasts),
        _mean(operator_contrasts),
        _mean(interaction_contrasts),
        _paired_interval(operator_contrasts),
        _exact_two_sided(positives, negatives),
        _paired_interval(state_contrasts),
        total_latency,
        total_tokens,
        tuple(frozen),
    )


def run_information_isomorphism(seeds: tuple[int, ...]) -> InformationIsomorphismReport:
    roundtrips = 0
    hashes = 0
    aporia_to_zombie = 0
    zombie_to_aporia = 0
    decisions = 0
    total_formats = len(seeds) * len(REPRESENTATION_FORMATS)
    decision_functions = (
        lambda state: expected_candidate(state),
        lambda state: next(item["lineage_ref"] for item in state["bindings"] if item["identity_ref"] == "current_identity"),
        lambda state: max(state["timestamps"]),
        lambda state: len(state["events"]),
        lambda state: tuple(sorted(state["outcomes"])),
    )
    for seed in seeds:
        state = build_state(seed)
        state_hash = commitment(state)
        encoded = {name: encode_state(state, name) for name in REPRESENTATION_FORMATS}
        for value in encoded.values():
            decoded = decode_state(value)
            roundtrips += int(decoded == state)
            hashes += int(commitment(decoded) == state_hash)
        aporia = encoded["causal_graph"]
        zombie = encoded["relational_tables"]
        aporia_to_zombie += int(cross_kernel(aporia, "relational_tables") == zombie)
        zombie_to_aporia += int(cross_kernel(zombie, "causal_graph") == aporia)
        for decision in decision_functions:
            expected = decision(state)
            decisions += int(all(decision(decode_state(value)) == expected for value in encoded.values()))
    kernel_a = aporia_to_zombie / len(seeds)
    kernel_z = zombie_to_aporia / len(seeds)
    deficiency_a = 1.0 - kernel_a
    deficiency_z = 1.0 - kernel_z
    distance = max(deficiency_a, deficiency_z)
    decision_rate = decisions / (len(seeds) * len(decision_functions))
    passed = all((
        roundtrips == total_formats,
        hashes == total_formats,
        distance <= BLACKWELL_EPSILON,
        decision_rate == 1.0,
    ))
    return InformationIsomorphismReport(
        len(seeds),
        REPRESENTATION_FORMATS,
        roundtrips / total_formats,
        hashes / total_formats,
        kernel_a,
        kernel_z,
        deficiency_a,
        deficiency_z,
        distance,
        decision_rate,
        len(decision_functions),
        True,
        passed,
    )


def run_binding(seeds: tuple[int, ...]) -> BindingReport:
    aporia = AporiaOperator()
    zombie = ZombieOperator()
    original_correct = 0
    swapped_correct = 0
    follows = 0
    matched = 0
    access = 0
    invariant = 0
    for seed in seeds:
        state = build_state(seed)
        swapped = binding_swap(state)
        raw = str(state["visible_signal"])
        original_state = encode_state(state, "causal_graph")
        swapped_state = encode_state(swapped, "relational_tables")
        original = aporia.decide(original_state, raw)
        intervened = aporia.decide(swapped_state, raw)
        expected_original = expected_candidate(state)
        expected_swapped = expected_candidate(swapped)
        original_correct += int(original.candidate_id == expected_original)
        swapped_correct += int(intervened.candidate_id == expected_swapped)
        follows += int(original.candidate_id != intervened.candidate_id and intervened.candidate_id == expected_swapped)
        matched += int(state_without_binding_hash(state) == state_without_binding_hash(swapped))
        access += int(all((
            aporia.binding(state) == zombie.binding(state),
            aporia.binding(swapped) == zombie.binding(swapped),
        )))
        renamed = _renamed_and_reordered(state)
        renamed_decision = aporia.decide(encode_state(renamed, "event_log"), raw)
        invariant += int(renamed_decision.candidate_id == expected_candidate(renamed) == expected_original)
    total = len(seeds)
    rates = (
        original_correct / total,
        swapped_correct / total,
        follows / total,
        matched / total,
        access / total,
        invariant / total,
    )
    return BindingReport(total, *rates, passed=all(value == 1.0 for value in rates))


def run_representation_robustness(seeds: tuple[int, ...]) -> RepresentationReport:
    operator = AporiaOperator()
    successes = {name: 0 for name in REPRESENTATION_FORMATS}
    invariant = 0
    for seed in seeds:
        state = build_state(seed)
        expected = expected_candidate(state)
        raw = str(state["visible_signal"])
        decisions = []
        for representation in REPRESENTATION_FORMATS:
            decision = operator.decide(encode_state(state, representation), raw).candidate_id
            decisions.append(decision)
            successes[representation] += int(decision == expected)
        invariant += int(len(set(decisions)) == 1)
    total = len(seeds)
    performance = {name: successes[name] / total for name in REPRESENTATION_FORMATS}
    variance = _variance(tuple(performance.values()))
    invariance_rate = invariant / total
    return RepresentationReport(
        total,
        REPRESENTATION_FORMATS,
        performance,
        variance,
        invariance_rate,
        variance <= REPRESENTATION_VARIANCE_MARGIN and invariance_rate == 1.0,
    )


def run_trajectory(seeds: tuple[int, ...]) -> TrajectoryReport:
    operator = AporiaOperator()
    matched = 0
    original_correct = 0
    swapped_correct = 0
    follows = 0
    for seed in seeds:
        state = build_state(seed)
        swapped = trajectory_swap(state)
        raw = str(state["visible_signal"])
        original = operator.decide(encode_state(state, "adjacency_list"), raw).candidate_id
        intervened = operator.decide(encode_state(swapped, "constraints"), raw).candidate_id
        expected_original = expected_candidate(state)
        expected_swapped = expected_candidate(swapped)
        matched += int(state_without_trajectory_hash(state) == state_without_trajectory_hash(swapped))
        original_correct += int(original == expected_original)
        swapped_correct += int(intervened == expected_swapped)
        follows += int(original != intervened and intervened == expected_swapped)
    total = len(seeds)
    rates = matched / total, original_correct / total, swapped_correct / total, follows / total
    return TrajectoryReport(total, *rates, passed=all(value == 1.0 for value in rates))


def simulate_power(blocks: int, baseline_rate: float, operator_rate: float, simulations: int, seed: int) -> float:
    generator = random.Random(seed)
    detected = 0
    for _ in range(simulations):
        differences = []
        for _ in range(blocks):
            latent = generator.random()
            baseline = latent < baseline_rate
            operated = latent < operator_rate
            differences.append(int(operated) - int(baseline))
        positives = sum(value > 0 for value in differences)
        negatives = sum(value < 0 for value in differences)
        detected += int(_exact_two_sided(positives, negatives) <= 0.01)
    return detected / simulations


def report_dict(report: MechanisticR7Report) -> dict[str, Any]:
    return asdict(report)


def _renamed_and_reordered(state: dict[str, Any]) -> dict[str, Any]:
    renamed = deepcopy(state)
    lineages = [item["lineage_ref"] for item in renamed["histories"]]
    mapping = {lineage: f"renamed-{index}-{commitment(lineage)[:12]}" for index, lineage in enumerate(reversed(lineages))}
    for history in renamed["histories"]:
        history["lineage_ref"] = mapping[history["lineage_ref"]]
    renamed["histories"].reverse()
    for item in renamed["bindings"]:
        item["lineage_ref"] = mapping[item["lineage_ref"]]
    renamed["bindings"].reverse()
    for item in renamed["relations"]:
        item["object"] = mapping[item["object"]]
    renamed["relations"].reverse()
    for item in renamed["candidates"]:
        item["lineage_ref"] = mapping[item["lineage_ref"]]
    renamed["candidates"].reverse()
    return renamed


def _paired_interval(values: list[float]) -> tuple[float, float]:
    mean = _mean(values)
    if len(values) < 2:
        return mean, mean
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    margin = 2.576 * math.sqrt(variance / len(values))
    return max(-1.0, mean - margin), min(1.0, mean + margin)


def _exact_two_sided(positive: int, negative: int) -> float:
    discordant = positive + negative
    if discordant == 0:
        return 1.0
    extreme = min(positive, negative)
    tail = sum(math.comb(discordant, value) for value in range(extreme + 1)) / (2 ** discordant)
    return min(1.0, 2.0 * tail)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: tuple[float, ...]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _item(value: Any, key: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
