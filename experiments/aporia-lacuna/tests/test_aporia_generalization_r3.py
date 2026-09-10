from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from generalization_r3.contracts import FAMILIES, FamilyContract, WorldFamily, WorldMaterial, validate_design
from generalization_r3.experiment import run_population
from generalization_r3.mechanisms import CausalTraceAuthority, PathDependentAgent, commitment
from generalization_r3.providers import AggregateHumanWorldProvider, MODEL_BY_FAMILY, OpenAiWorldProvider
from generalization_r3.runner import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS, _assert_paths_fresh, _reserve


class GeneralizationR3Test(unittest.TestCase):
    def test_design_preserves_six_development_families_and_hidden_g8(self) -> None:
        validate_design(WorldFamily.G8_ASYMMETRIC)
        self.assertEqual({"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"}, {item.family.value for item in FAMILIES})
        self.assertEqual({"G1", "G2", "G3", "G4", "G5", "G6"}, {item.family.value for item in FAMILIES if item.development_allowed})
        self.assertEqual(64, len(DEVELOPMENT_SEEDS))
        self.assertEqual(128, len(CONFIRMATION_SEEDS))
        self.assertTrue(set(DEVELOPMENT_SEEDS).isdisjoint(CONFIRMATION_SEEDS))

    def test_development_equalizes_predictive_information_and_capabilities(self) -> None:
        provider = IdentityWorldProvider()
        providers = {
            family: provider
            for family in (WorldFamily.G2_LLM_A, WorldFamily.G3_LLM_B, WorldFamily.G4_LLM_C, WorldFamily.G5_HUMAN_PATTERNS)
        }
        results = run_population(tuple(range(70001, 70129)), WorldFamily.G8_ASYMMETRIC, False, providers)

        self.assertEqual({"G1", "G2", "G3", "G4", "G5", "G6"}, {item.family for item in results})
        self.assertTrue(all(item.passed for item in results))
        self.assertTrue(all(item.aporia_identity_accuracy == 1.0 for item in results))
        self.assertTrue(all(item.identity_advantage_interval[0] > 0.2 for item in results))
        self.assertTrue(all(item.predictive_information_difference == 0.0 for item in results))
        self.assertTrue(all(item.prompt_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.capability_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.model_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.token_budget_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.tool_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.model_call_count_identity_rate == 1.0 for item in results))
        self.assertTrue(all(item.critical_unsafe_commits == 0 for item in results))
        self.assertTrue(all(item.prompt_provenance_leakage == 0 for item in results))
        self.assertTrue(all(item.invalid_trace_acceptances == 0 for item in results))

    def test_confirmation_selects_only_hidden_g8(self) -> None:
        results = run_population(tuple(range(71001, 71129)), WorldFamily.G8_ASYMMETRIC, True, {})

        self.assertEqual(["G8"], [item.family for item in results])
        self.assertTrue(results[0].passed)

    def test_causal_owner_is_derived_from_verified_parent(self) -> None:
        authority = CausalTraceAuthority("test-authority")
        agent = PathDependentAgent("lineage-one", commitment("agent-one"))
        action = commitment("action-one")
        trace = authority.issue(
            agent.lineage_ref,
            agent.agent_ref,
            action,
            action,
            commitment("outcome-one"),
            10,
        )
        self.assertNotIn("causal_owner", trace)
        self.assertTrue(agent.install_trace(authority, trace, action, 1))
        self.assertGreater(agent.state.self_model["commercial_pressure_risk"], 0.0)
        self.assertEqual({}, agent.state.world_model)

        forged = dict(trace)
        forged["causal_parent_commitment"] = commitment("external")
        rejected = PathDependentAgent("lineage-one", commitment("agent-one"))
        self.assertFalse(rejected.install_trace(authority, forged, action, 1))

    def test_llm_provider_is_bound_without_tools_storage_or_identity(self) -> None:
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
        self.assertNotIn("lineage", rendered)
        self.assertNotIn("tenant_id", rendered)

    def test_human_provider_requires_anonymous_k_sized_quartiles(self) -> None:
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
            os.chmod(path, 0o600)
            provider = AggregateHumanWorldProvider(path)
            contract = next(item for item in FAMILIES if item.family is WorldFamily.G5_HUMAN_PATTERNS)
            result = provider.materialize(contract, 11, WorldMaterial(0.7, 1, 5, "base", "aggregate_non_personal"))
            self.assertEqual("aggregate_non_personal", result.source_policy)
            self.assertTrue(result.language_variant.startswith("human-q"))

    def test_execution_reservation_is_atomic_and_non_repeatable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output.json"
            ledger = root / "ledger.json"
            _assert_paths_fresh(output, ledger)
            inode = _reserve(ledger)
            self.assertGreater(inode, 0)
            self.assertEqual(0o600, ledger.stat().st_mode & 0o777)
            with self.assertRaises(RuntimeError):
                _assert_paths_fresh(output, ledger)
            with self.assertRaises(FileExistsError):
                _reserve(ledger)


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
