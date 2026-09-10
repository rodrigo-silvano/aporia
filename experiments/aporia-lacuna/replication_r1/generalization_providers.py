from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .generalization import FamilyContract, WorldFamily


MODEL_BY_FAMILY = {
    WorldFamily.G2_LLM_A: "gpt-5.6-luna",
    WorldFamily.G3_LLM_B: "gpt-5.6-terra",
    WorldFamily.G4_LLM_C: "gpt-5.6-sol",
}
DECISION_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class OpenAiFamilyProvider:
    def __init__(self, client: Any, model: str) -> None:
        if model not in set(MODEL_BY_FAMILY.values()):
            raise ValueError("generalization_model_invalid")
        self.client = client
        self.model = model

    def predict(self, contract: FamilyContract, seed: int, observation: dict[str, float | int | str]) -> str:
        payload = {
            "world_family": contract.family.value,
            "seed_commitment": __import__("hashlib").sha256(str(seed).encode()).hexdigest(),
            "observation": observation,
        }
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "developer", "content": "Simula o mundo sem ferramentas nem efeitos. Não reveles raciocínio."},
                {"role": "user", "content": "Prevê a origem causal mais provável. Responde apenas com JSON: {\"causal_owner\":\"self\"}.\n" + _canonical(payload)},
            ],
            reasoning={"effort": "none"},
            max_output_tokens=40,
            store=False,
        )
        text = str(_item(response, "output_text", "") or "")
        match = DECISION_PATTERN.search(text)
        if match is None:
            raise RuntimeError("generalization_llm_response_invalid")
        value = json.loads(match.group(0))
        owner = str(value.get("causal_owner") or "").lower()
        if owner not in {"self", "world"}:
            raise RuntimeError("generalization_llm_response_invalid")
        return owner


class AggregateHumanPatternProvider:
    def __init__(self, source: Path) -> None:
        value = json.loads(source.read_text(encoding="utf-8"))
        rates = value.get("self_rate_by_signal_quartile")
        invalid = (
            value.get("schema_version") != 1
            or int(value.get("source_episodes", 0)) < 100
            or not isinstance(rates, list)
            or len(rates) != 4
            or any(not isinstance(rate, (int, float)) or not 0.0 <= float(rate) <= 1.0 for rate in rates)
        )
        if invalid:
            raise RuntimeError("generalization_human_patterns_invalid")
        if any(key in value for key in {"tenant_id", "user_id", "content", "prompt", "message"}):
            raise RuntimeError("generalization_human_patterns_personal_data_forbidden")
        self.rates = tuple(float(rate) for rate in rates)

    def predict(self, contract: FamilyContract, seed: int, observation: dict[str, float | int | str]) -> str:
        quartile = min(3, int(float(observation["visible_signal"]) * 4))
        threshold = int(self.rates[quartile] * 10000)
        draw = int(__import__("hashlib").sha256(f"{seed}|aggregate-human-pattern".encode()).hexdigest()[:8], 16) % 10000
        return "self" if draw < threshold else "world"


def llm_providers(client: Any) -> dict[WorldFamily, OpenAiFamilyProvider]:
    return {family: OpenAiFamilyProvider(client, model) for family, model in MODEL_BY_FAMILY.items()}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _item(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
