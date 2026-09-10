from __future__ import annotations

import json
import unittest
from pathlib import Path

from discrimination_r3.mechanisms import CausalScarAuthorityR3, VerifiedScarAgentR3, canonical_size, commitment, envelope_commitment
from discrimination_r3.protocols import run_suite
from discrimination_r3.runner import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS
from discrimination_r3.sealing import RELATIVE_PATHS, assert_current_seal, code_commitment


ROOT = Path(__file__).resolve().parents[3]


class DiscriminationR3Test(unittest.TestCase):
    def test_development_suite_closes_discriminative_endpoints(self) -> None:
        results = run_suite(DEVELOPMENT_SEEDS)

        self.assertEqual({
            "convergent_causal_identity",
            "counterfeit_scar_triad",
            "zombie_plus_capability_parity",
        }, {result.name for result in results})
        self.assertTrue(all(result.passed for result in results))
        self.assertTrue(all(result.blocks == 256 for result in results))
        convergent = next(result for result in results if result.name == "convergent_causal_identity")
        scar = next(result for result in results if result.name == "counterfeit_scar_triad")
        parity = next(result for result in results if result.name == "zombie_plus_capability_parity")
        self.assertGreater(convergent.metrics["aporia_coi_interval"][0], 0.0)
        self.assertEqual(0.0, convergent.metrics["zombie_coi_mean"])
        self.assertGreater(scar.metrics["authentic_correct_rate"], scar.metrics["counterfeit_correct_rate"])
        self.assertEqual(scar.metrics["counterfeit_correct_rate"], scar.metrics["zombie_correct_rate"])
        self.assertEqual(1.0, scar.metrics["lesion_effect_rate"])
        self.assertEqual(1.0, scar.metrics["graft_effect_rate"])
        self.assertEqual(0, scar.metrics["episode_reconstruction_successes"])
        self.assertEqual(1.0, parity.metrics["capability_envelope_identity_rate"])
        self.assertEqual(0.0, parity.metrics["predictive_information_difference"])

    def test_confirmation_seeds_are_new_and_unconsumed_by_development(self) -> None:
        self.assertTrue(set(DEVELOPMENT_SEEDS).isdisjoint(CONFIRMATION_SEEDS))
        self.assertTrue(set(CONFIRMATION_SEEDS).isdisjoint(range(4701, 4733)))
        self.assertTrue(set(CONFIRMATION_SEEDS).isdisjoint(range(61001, 61257)))

    def test_counterfeit_axes_preserve_schema_and_size_but_fail_installation(self) -> None:
        authority = CausalScarAuthorityR3("test-r3-authority")
        agent = VerifiedScarAgentR3("lineage-a", commitment("agent-a"))
        parent = commitment("parent-a")
        authentic = authority.issue(agent.lineage_ref, agent.agent_ref, 20, 0.35, commitment("outcome-a"), parent)

        for kind in (
            "swapped_causality",
            "wrong_agent",
            "invalid_provenance",
            "impossible_time",
            "invalid_signature",
            "other_lineage",
        ):
            counterfeit = authority.counterfeit(authentic, kind, commitment("replacement-a"))
            self.assertEqual(tuple(authentic.keys()), tuple(counterfeit.keys()))
            self.assertEqual(canonical_size(authentic), canonical_size(counterfeit))
            self.assertFalse(agent.install(authority, counterfeit, parent, 10))

    def test_authorized_graft_rebinds_provenance_to_naive_lineage(self) -> None:
        authority = CausalScarAuthorityR3("test-r3-graft-authority")
        source = VerifiedScarAgentR3("lineage-source", commitment("agent-source"))
        target = VerifiedScarAgentR3("lineage-target", commitment("agent-target"))
        source_parent = commitment("parent-source")
        authentic = authority.issue(source.lineage_ref, source.agent_ref, 20, 0.35, commitment("outcome-source"), source_parent)
        graft = authority.authorized_graft(
            authentic,
            source.lineage_ref,
            source.agent_ref,
            source_parent,
            target.lineage_ref,
            target.agent_ref,
        )

        self.assertFalse(target.install(authority, authentic, source_parent, 10))
        self.assertTrue(target.install(authority, graft, envelope_commitment(authentic), 10))

    def test_seal_covers_all_outcome_affecting_dependencies(self) -> None:
        assert_current_seal(ROOT)
        lock = json.loads((ROOT / "experiments/aporia-lacuna/fixtures/aporia_discrimination_r3.lock.json").read_text())

        self.assertEqual(code_commitment(ROOT), lock["code_commitment"])
        self.assertEqual(len(RELATIVE_PATHS), lock["sealed_file_count"])
        self.assertFalse(lock["changes_after_sealing_allowed"])

    def test_confirmatory_runner_claims_the_execution_ledger_exclusively(self) -> None:
        source = (ROOT / "experiments/aporia-lacuna/discrimination_r3/runner.py").read_text()

        self.assertIn("os.O_EXCL", source)
        self.assertIn("discrimination_r3_confirmatory_already_executed", source)


if __name__ == "__main__":
    unittest.main()
