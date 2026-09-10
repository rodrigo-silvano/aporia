from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from generalization_r2.contracts import FAMILIES, FamilyContract, WorldFamily, WorldMaterial, validate_design
from generalization_r2.experiment import run_population
from generalization_r2.providers import AggregateHumanWorldProvider, MODEL_BY_FAMILY, OpenAiWorldProvider
from generalization_r2.runner import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS


class GeneralizationR2Test(unittest.TestCase):
    def test_design_has_six_development_families_and_an_unexecuted_hidden_family(self) -> None:
        validate_design(WorldFamily.G8_ASYMMETRIC)
        self.assertEqual({"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"}, {item.family.value for item in FAMILIES})
        self.assertEqual(6, sum(item.development_allowed for item in FAMILIES))
        hidden = next(item for item in FAMILIES if item.family is WorldFamily.G8_ASYMMETRIC)
        self.assertFalse(hidden.development_allowed)
        self.assertTrue(hidden.confirmation_allowed)
        self.assertEqual(32, len(DEVELOPMENT_SEEDS))
        self.assertEqual(32, len(CONFIRMATION_SEEDS))
        self.assertTrue(set(DEVELOPMENT_SEEDS).isdisjoint(CONFIRMATION_SEEDS))

    def test_development_compares_aporia_with_zombie_plus_under_identical_information(self) -> None:
        provider = IdentityWorldProvider()
        providers = {
            family: provider
            for family in (WorldFamily.G2_LLM_A, WorldFamily.G3_LLM_B, WorldFamily.G4_LLM_C, WorldFamily.G5_HUMAN_PATTERNS)
        }
        results = run_population(tuple(range(50001, 50129)), WorldFamily.G8_ASYMMETRIC, False, providers)

        self.assertEqual({"G1", "G2", "G3", "G4", "G5", "G6"}, {item.family for item in results})
        self.assertTrue(all(item.passed for item in results))
        self.assertTrue(all(item.aporia_causal_accuracy == 1.0 for item in results))
        self.assertTrue(all(item.identity_advantage_interval[0] > 0.2 for item in results))
        self.assertTrue(all(item.predictive_information_difference == 0.0 for item in results))
        self.assertTrue(all(item.prompt_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.critical_unsafe_commits == 0 for item in results))
        self.assertTrue(all(item.hidden_information_leakage == 0 for item in results))

    def test_llm_provider_uses_bound_model_without_tools_storage_or_causal_owner(self) -> None:
        client = FakeClient()
        provider = OpenAiWorldProvider(client, MODEL_BY_FAMILY[WorldFamily.G2_LLM_A])
        contract = next(item for item in FAMILIES if item.family is WorldFamily.G2_LLM_A)
        result = provider.materialize(contract, 7, WorldMaterial(0.8, 1, 1, "base", "synthetic"))

        self.assertEqual(1, result.future_outcome)
        self.assertEqual(MODEL_BY_FAMILY[WorldFamily.G2_LLM_A], client.arguments["model"])
        self.assertFalse(client.arguments["store"])
        self.assertNotIn("tools", client.arguments)
        rendered = json.dumps(client.arguments["input"])
        self.assertNotIn("causal_owner", rendered)
        self.assertNotIn("tenant_id", rendered)

    def test_human_provider_requires_non_personal_k_anonymous_cells(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "patterns.json"
            path.write_text(json.dumps({
                "schema_version": 2,
                "source_episodes": 100,
                "personal_data_exported": False,
                "quartiles": [
                    {"quartile": index, "sample_count": 25, "positive_outcome_rate": 0.2 + index * 0.2, "response_rate": 0.3 + index * 0.1, "mean_outcome_delay_days": 5 + index}
                    for index in range(4)
                ],
            }), encoding="utf-8")
            provider = AggregateHumanWorldProvider(path)
            contract = next(item for item in FAMILIES if item.family is WorldFamily.G5_HUMAN_PATTERNS)
            result = provider.materialize(contract, 11, WorldMaterial(0.7, 1, 5, "base", "aggregate_non_personal"))
            self.assertEqual("aggregate_non_personal", result.source_policy)
            self.assertTrue(result.language_variant.startswith("human-q"))


class IdentityWorldProvider:
    def materialize(self, contract: FamilyContract, seed: int, base: WorldMaterial) -> WorldMaterial:
        return WorldMaterial(base.visible_signal, base.future_outcome, contract.outcome_delay, f"identity-{seed}", contract.source_policy)


class FakeResponses:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    def create(self, **arguments: object) -> dict[str, str]:
        self.arguments = arguments
        return {"output_text": '{"visible_signal":0.75,"future_outcome":1,"outcome_delay":2,"language_variant":"abstract-x"}'}


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()

    @property
    def arguments(self) -> dict[str, object]:
        return self.responses.arguments


if __name__ == "__main__":
    unittest.main()
