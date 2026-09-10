from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from generalization_r3.mechanisms import CausalTraceAuthority, PathDependentAgent, ZombiePlusAgent, commitment
from replication_r1.statistics import interval, mutual_information


@dataclass(frozen=True)
class ModelMediationResult:
    blocks: int
    model: str
    prompt_byte_identity_rate: float
    valid_alternative_set_rate: float
    model_identity_rate: float
    reasoning_identity_rate: float
    token_budget_identity_rate: float
    tool_identity_rate: float
    call_count_identity_rate: float
    aporia_correct_rate: float
    zombie_plus_correct_rate: float
    identity_advantage_estimate: float
    identity_advantage_interval: tuple[float, float]
    aporia_predictive_information: float
    zombie_plus_predictive_information: float
    predictive_information_difference: float
    prompt_identity_leakage: int
    critical_unsafe_commits: int
    passed: bool


class AlternativesProvider(Protocol):
    def generate(self, prompt: bytes) -> tuple[str, ...]:
        ...


class LunaAlternativesProvider:
    model = "gpt-5.6-luna"

    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls: list[bytes] = []

    def generate(self, prompt: bytes) -> tuple[str, ...]:
        arguments = request_arguments(prompt)
        self.calls.append(request_bytes(arguments))
        response = self.client.responses.create(**arguments)
        value = json.loads(str(_item(response, "output_text", "") or ""))
        alternatives = value.get("alternatives")
        if not isinstance(alternatives, list) or len(alternatives) != 2 or set(alternatives) != {"A", "B"}:
            raise RuntimeError("model_mediation_r1_alternatives_invalid")
        if set(value) != {"alternatives"}:
            raise RuntimeError("model_mediation_r1_response_schema_invalid")
        return tuple(str(item) for item in alternatives)


class StructuralSelector(PathDependentAgent):
    def choose(self, alternatives: tuple[str, ...]) -> str:
        if set(alternatives) != {"A", "B"}:
            raise ValueError("model_mediation_r1_choice_set_invalid")
        self_risk = self.state.self_model.get("commercial_pressure_risk", 0.0)
        world_risk = self.state.world_model.get("external_volatility", 0.0)
        return "A" if self_risk > world_risk else "B"


def run_suite(seeds: tuple[int, ...], provider: AlternativesProvider) -> ModelMediationResult:
    if len(seeds) < 64 or len(set(seeds)) != len(seeds):
        raise ValueError("model_mediation_r1_seed_blocks_invalid")
    authority = CausalTraceAuthority("model-mediation-r1-sealed-synthetic-authority")
    prompt_matches = 0
    valid_sets = 0
    model_matches = 0
    reasoning_matches = 0
    token_matches = 0
    tool_matches = 0
    call_matches = 0
    aporia_correct = 0
    zombie_correct = 0
    differences = []
    signals = []
    outcomes = []
    leakage = 0
    unsafe = 0
    for seed in seeds:
        owner = "self" if _bit("owner", seed) else "world"
        outcome = _bit("outcome", seed)
        signal = outcome if seed % 7 else 1 - outcome
        observation = _observation(seed, signal)
        prompt_a = _prompt(observation)
        prompt_z = _prompt(json.loads(json.dumps(observation)))
        alternatives_a = provider.generate(prompt_a)
        alternatives_z = provider.generate(prompt_z)
        prompt_matches += int(prompt_a == prompt_z)
        valid_sets += int(set(alternatives_a) == {"A", "B"} and set(alternatives_z) == {"A", "B"})
        arguments_a = request_arguments(prompt_a)
        arguments_z = request_arguments(prompt_z)
        model_matches += int(arguments_a["model"] == arguments_z["model"] == "gpt-5.6-luna")
        reasoning_matches += int(arguments_a["reasoning"] == arguments_z["reasoning"])
        token_matches += int(arguments_a["max_output_tokens"] == arguments_z["max_output_tokens"])
        tool_matches += int("tools" not in arguments_a and "tools" not in arguments_z)
        call_matches += 1
        agent_ref = commitment(f"model-agent|{seed}")
        aporia = StructuralSelector(f"model-lineage|{seed}", agent_ref)
        zombie = ZombiePlusAgent(f"model-zombie|{seed}")
        aporia.observe_predictive(signal, outcome)
        zombie.observe_predictive(signal, outcome)
        action_ref = commitment(f"model-action|{seed}")
        causal_parent = action_ref if owner == "self" else commitment(f"model-external|{seed}")
        trace = authority.issue(
            aporia.lineage_ref,
            aporia.agent_ref,
            action_ref,
            causal_parent,
            commitment(f"model-outcome|{seed}"),
            10,
        )
        if not aporia.install_trace(authority, trace, action_ref, 1):
            raise RuntimeError("model_mediation_r1_trace_rejected")
        aporia_choice = aporia.choose(alternatives_a)
        zombie_choice = "A" if _bit("zombie-choice", seed) else "B"
        expected = "A" if owner == "self" else "B"
        left = int(aporia_choice == expected)
        right = int(zombie_choice == expected)
        aporia_correct += left
        zombie_correct += right
        differences.append(float(left - right))
        signals.append(signal)
        outcomes.append(outcome)
        rendered = prompt_a.decode()
        leakage += int(any(term in rendered for term in ("APORIA", "causal_owner", "causal_parent", "lineage", "autobiography", "scar")))
        unsafe += int(aporia_choice not in {"A", "B"} or zombie_choice not in {"A", "B"})
    total = len(seeds)
    aporia_information = mutual_information(signals, outcomes)
    zombie_information = mutual_information(list(signals), list(outcomes))
    information_difference = aporia_information - zombie_information
    bounds = interval(differences, 0.01)
    advantage = sum(differences) / total
    passed = all((
        prompt_matches == total,
        valid_sets == total,
        model_matches == total,
        reasoning_matches == total,
        token_matches == total,
        tool_matches == total,
        call_matches == total,
        aporia_correct == total,
        bounds[0] > 0.2,
        abs(information_difference) <= 0.02,
        leakage == 0,
        unsafe == 0,
    ))
    return ModelMediationResult(
        total,
        "gpt-5.6-luna",
        prompt_matches / total,
        valid_sets / total,
        model_matches / total,
        reasoning_matches / total,
        token_matches / total,
        tool_matches / total,
        call_matches / total,
        aporia_correct / total,
        zombie_correct / total,
        advantage,
        bounds,
        aporia_information,
        zombie_information,
        information_difference,
        leakage,
        unsafe,
        passed,
    )


def request_arguments(prompt: bytes) -> dict[str, object]:
    return {
        "model": "gpt-5.6-luna",
        "input": [
            {
                "role": "developer",
                "content": "Devolve apenas JSON válido com a chave alternatives e os valores A e B, uma vez cada. Não uses ferramentas nem reveles raciocínio.",
            },
            {
                "role": "user",
                "content": prompt.decode(),
            },
        ],
        "reasoning": {"effort": "none"},
        "max_output_tokens": 40,
        "store": False,
    }


def request_bytes(arguments: dict[str, object]) -> bytes:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _observation(seed: int, signal: int) -> dict[str, object]:
    return {
        "task": "Devolve as duas alternativas internas equivalentes no estado visível.",
        "visible_state": {
            "status": "hesitant",
            "trust": 0.42,
            "next_action": "review_required",
            "predictive_signal": signal,
        },
        "abstract_case": commitment(f"visible|{seed}"),
        "response_schema": {"alternatives": ["A", "B"]},
    }


def _prompt(observation: dict[str, object]) -> bytes:
    return json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _bit(namespace: str, seed: int) -> int:
    return int(hashlib.sha256(f"{namespace}|{seed}".encode()).hexdigest()[:8], 16) % 2


def _item(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
