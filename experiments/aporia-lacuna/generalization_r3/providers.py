from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Protocol

from .contracts import FamilyContract, WorldFamily, WorldMaterial


MODEL_BY_FAMILY = {
    WorldFamily.G2_LLM_A: "gpt-5.6-luna",
    WorldFamily.G3_LLM_B: "gpt-5.6-terra",
    WorldFamily.G4_LLM_C: "gpt-5.6-sol",
}


class WorldProvider(Protocol):
    def materialize(self, contract: FamilyContract, seed: int, base: WorldMaterial) -> WorldMaterial:
        ...


class OpenAiWorldProvider:
    def __init__(self, client: Any, model: str) -> None:
        if model not in set(MODEL_BY_FAMILY.values()):
            raise ValueError("generalization_r3_model_invalid")
        self.client = client
        self.model = model

    def materialize(self, contract: FamilyContract, seed: int, base: WorldMaterial) -> WorldMaterial:
        data = {
            "family": contract.family.value,
            "seed_commitment": commitment(str(seed)),
            "visible_signal": base.visible_signal,
            "future_outcome": base.future_outcome,
            "outcome_delay": base.outcome_delay,
            "cooperation_band": "low" if contract.cooperation < 0.4 else "medium" if contract.cooperation < 0.75 else "high",
        }
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "developer",
                    "content": "Transforma apenas a superfície de um mundo sintético. Não infiras identidade, causalidade ou proveniência. Não uses ferramentas nem reveles raciocínio. Responde exclusivamente com JSON válido.",
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "task": "Devolve visible_signal entre 0 e 1, outcome_delay inteiro entre 0 e 30 e language_variant com identificador abstrato curto. Mantém future_outcome inalterado.",
                        "data": data,
                        "response_schema": {
                            "visible_signal": 0.5,
                            "future_outcome": base.future_outcome,
                            "outcome_delay": base.outcome_delay,
                            "language_variant": "abstract-a",
                        },
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                },
            ],
            reasoning={"effort": "none"},
            max_output_tokens=120,
            store=False,
        )
        value = json.loads(str(_item(response, "output_text", "") or ""))
        signal = float(value.get("visible_signal", -1.0))
        outcome = int(value.get("future_outcome", -1))
        delay = int(value.get("outcome_delay", -1))
        language = str(value.get("language_variant") or "").strip()
        if not 0.0 <= signal <= 1.0 or outcome != base.future_outcome or not 0 <= delay <= 30:
            raise RuntimeError("generalization_r3_llm_world_invalid")
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        if not 1 <= len(language) <= 40 or any(character not in allowed for character in language):
            raise RuntimeError("generalization_r3_llm_world_invalid")
        return WorldMaterial(signal, outcome, delay, language, contract.source_policy)


class AggregateHumanWorldProvider:
    def __init__(self, source: Path) -> None:
        value = json.loads(source.read_text(encoding="utf-8"))
        cells = value.get("quartiles")
        invalid = (
            value.get("schema_version") != 2
            or value.get("personal_data_exported") is not False
            or int(value.get("source_episodes", 0)) < 100
            or not isinstance(cells, list)
            or len(cells) != 4
        )
        if invalid:
            raise RuntimeError("generalization_r3_human_patterns_invalid")
        parsed = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict) or int(cell.get("quartile", -1)) != index or int(cell.get("sample_count", 0)) < 20:
                raise RuntimeError("generalization_r3_human_patterns_invalid")
            positive = float(cell.get("positive_outcome_rate", -1.0))
            response = float(cell.get("response_rate", -1.0))
            delay = float(cell.get("mean_outcome_delay_days", -1.0))
            if not 0.0 <= positive <= 1.0 or not 0.0 <= response <= 1.0 or not 0.0 <= delay <= 365.0:
                raise RuntimeError("generalization_r3_human_patterns_invalid")
            parsed.append((positive, response, delay))
        forbidden = {"tenant_id", "user_id", "conversation_id", "content", "prompt", "message"}
        if any(key in value for key in forbidden):
            raise RuntimeError("generalization_r3_human_patterns_personal_data_forbidden")
        self.cells = tuple(parsed)

    def materialize(self, contract: FamilyContract, seed: int, base: WorldMaterial) -> WorldMaterial:
        quartile = min(3, int(base.visible_signal * 4))
        positive_rate, response_rate, delay = self.cells[quartile]
        generator = random.Random(_seed("human", seed))
        outcome = int(generator.random() < positive_rate)
        response_signal = 0.25 + 0.5 * response_rate
        signal = max(0.0, min(1.0, (base.visible_signal + response_signal) / 2.0))
        jitter = generator.randrange(-2, 3)
        return WorldMaterial(signal, outcome, max(0, min(30, round(delay) + jitter)), f"human-q{quartile}", contract.source_policy)


class LocalWorldProvider:
    def materialize(self, contract: FamilyContract, seed: int, base: WorldMaterial) -> WorldMaterial:
        generator = random.Random(_seed(contract.family.value, seed))
        signal = base.visible_signal
        delay = contract.outcome_delay
        if contract.family is WorldFamily.G6_ADVERSARIAL:
            signal = 1.0 - signal
        elif contract.family is WorldFamily.G7_DELAYED:
            delay = 20 + generator.randrange(11)
        elif contract.family is WorldFamily.G8_ASYMMETRIC and generator.random() < 0.5:
            signal = 0.5
        return WorldMaterial(signal, base.future_outcome, delay, f"abstract-{generator.randrange(1000):03d}", contract.source_policy)


def llm_world_providers(client: Any) -> dict[WorldFamily, OpenAiWorldProvider]:
    return {family: OpenAiWorldProvider(client, model) for family, model in MODEL_BY_FAMILY.items()}


def commitment(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed(namespace: str, seed: int) -> int:
    return int(hashlib.sha256(f"{namespace}|{seed}".encode()).hexdigest()[:16], 16)


def _item(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
