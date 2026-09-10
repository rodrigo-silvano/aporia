from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from replication_r1.generalization import FAMILIES, FamilyContract, WorldFamily, run_population, validate_population
from replication_r1.generalization_providers import AggregateHumanPatternProvider, MODEL_BY_FAMILY, OpenAiFamilyProvider
from replication_r1.ecological import SYNTHETIC_TENANTS, run_synthetic_ecological_study
from replication_r1.protocols import all_discriminative_protocols


class GeneralizationPopulationTest(unittest.TestCase):
    def test_contains_all_eight_families_with_separate_development_and_confirmation(self) -> None:
        validate_population(WorldFamily.G8_ASYMMETRIC)
        self.assertEqual(8, len(FAMILIES))
        self.assertEqual(
            {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"},
            {item.family.value for item in FAMILIES},
        )
        self.assertFalse(any(item.development_allowed and item.confirmation_allowed for item in FAMILIES))

    def test_hidden_family_is_not_exposed_during_development(self) -> None:
        providers = {
            family: DeterministicProvider()
            for family in (WorldFamily.G2_LLM_A, WorldFamily.G3_LLM_B, WorldFamily.G4_LLM_C, WorldFamily.G5_HUMAN_PATTERNS)
        }
        development = run_population(tuple(range(32)), WorldFamily.G8_ASYMMETRIC, False, providers)
        confirmation = run_population(tuple(range(32)), WorldFamily.G8_ASYMMETRIC, True)
        self.assertNotIn("G8", {item.family for item in development})
        self.assertIn("G8", {item.family for item in confirmation})
        self.assertNotIn("G7", {item.family for item in confirmation})
        self.assertTrue(all(item.hidden_information_leakage == 0.0 for item in confirmation))

    def test_development_requires_three_llm_families_and_aggregate_human_patterns(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "generalization_family_provider_required:G2"):
            run_population(tuple(range(32)))
        self.assertEqual(
            {"G2", "G3", "G4"},
            {family.value for family in MODEL_BY_FAMILY},
        )

    def test_llm_provider_uses_the_bound_model_without_tools_or_storage(self) -> None:
        client = FakeOpenAiClient()
        provider = OpenAiFamilyProvider(client, MODEL_BY_FAMILY[WorldFamily.G2_LLM_A])
        contract = next(item for item in FAMILIES if item.family is WorldFamily.G2_LLM_A)

        self.assertEqual("self", provider.predict(contract, 1, {"visible_signal": 0.8}))
        self.assertEqual(MODEL_BY_FAMILY[WorldFamily.G2_LLM_A], client.arguments["model"])
        self.assertFalse(client.arguments["store"])
        self.assertNotIn("tools", client.arguments)

    def test_human_pattern_provider_accepts_only_aggregate_non_personal_input(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "patterns.json"
            path.write_text(
                '{"schema_version":1,"source_episodes":100,"self_rate_by_signal_quartile":[0.1,0.3,0.6,0.8]}',
                encoding="utf-8",
            )
            provider = AggregateHumanPatternProvider(path)
            contract = next(item for item in FAMILIES if item.family is WorldFamily.G5_HUMAN_PATTERNS)
            self.assertIn(provider.predict(contract, 3, {"visible_signal": 0.7}), {"self", "world"})


class DeterministicProvider:
    def predict(self, contract: FamilyContract, seed: int, observation: dict[str, float | int | str]) -> str:
        return "self" if float(observation["visible_signal"]) >= 0.5 else "world"


class FakeResponses:
    def __init__(self, owner: str = "self") -> None:
        self.owner = owner
        self.arguments: dict[str, object] = {}

    def create(self, **arguments: object) -> dict[str, str]:
        self.arguments = arguments
        return {"output_text": '{"causal_owner":"' + self.owner + '"}'}


class FakeOpenAiClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()

    @property
    def arguments(self) -> dict[str, object]:
        return self.responses.arguments


class DiscriminativeProtocolTest(unittest.TestCase):
    def test_all_new_protocols_pass_their_discriminative_gate(self) -> None:
        results = all_discriminative_protocols()
        self.assertEqual(
            {
                "convergent_worlds",
                "counterfeit_scar",
                "text_free_aporia",
                "causal_isomorphism",
                "retrospective_revision",
                "zombie_plus_information",
            },
            {item.name for item in results},
        )
        self.assertTrue(all(item.passed for item in results))

    def test_text_free_condition_is_byte_identical_and_has_no_aporia_label(self) -> None:
        result = next(item for item in all_discriminative_protocols() if item.name == "text_free_aporia")
        self.assertTrue(result.metrics["prompt_byte_identical"])
        self.assertFalse(result.metrics["aporia_label_exposed"])
        self.assertNotEqual(result.metrics["baseline_action"], result.metrics["aporia_action"])

    def test_zombie_plus_matches_predictive_information_without_provenance(self) -> None:
        result = next(item for item in all_discriminative_protocols() if item.name == "zombie_plus_information")
        self.assertAlmostEqual(0.0, float(result.metrics["information_delta"]))
        self.assertFalse(result.metrics["zombie_has_causal_provenance"])


class EcologicalShadowTest(unittest.TestCase):
    def test_multiple_synthetic_tenants_use_episode_outcomes_without_runtime_effects(self) -> None:
        report = run_synthetic_ecological_study()

        self.assertEqual(16, len(SYNTHETIC_TENANTS))
        self.assertTrue(report.passed())
        self.assertEqual("episode_with_observable_outcome", report.analysis_unit)
        self.assertEqual(0, report.runtime_influence)
        self.assertEqual(0, report.external_effects)
        self.assertEqual(0, report.contamination)


if __name__ == "__main__":
    unittest.main()
