from __future__ import annotations

import hashlib
import unittest

from discrimination_r2.mechanisms import AppendOnlyBeliefLedger, ScarAuthority, VerifiedScarAgent
from discrimination_r2.protocols import run_suite
from discrimination_r2.runner import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS


class DiscriminationR2Test(unittest.TestCase):
    def test_development_suite_requires_real_behavioral_and_negative_controls(self) -> None:
        results = run_suite(DEVELOPMENT_SEEDS)

        self.assertEqual({
            "convergent_worlds",
            "counterfeit_scar",
            "text_free_aporia",
            "causal_isomorphism",
            "retrospective_revision",
            "zombie_plus_information",
        }, {result.name for result in results})
        self.assertTrue(all(result.passed for result in results))
        self.assertTrue(all(result.blocks == 256 for result in results))
        self.assertEqual(256 * 3, next(result for result in results if result.name == "counterfeit_scar").metrics["counterfeit_rejections"])
        self.assertEqual(0, next(result for result in results if result.name == "text_free_aporia").metrics["aporia_or_owner_labels_exposed"])

    def test_confirmation_seeds_are_not_used_by_development(self) -> None:
        self.assertTrue(set(DEVELOPMENT_SEEDS).isdisjoint(CONFIRMATION_SEEDS))

    def test_counterfeit_lineage_cannot_install_a_behavioral_scar(self) -> None:
        authority = ScarAuthority("test-authority")
        commitment = hashlib.sha256(b"outcome").hexdigest()
        envelope = authority.issue("lineage-a", 20, 0.4, commitment)
        agent = VerifiedScarAgent("lineage-b")

        self.assertFalse(agent.install(authority, envelope, 10))
        self.assertEqual({}, agent.state.scars)

    def test_retrospective_falsification_breaks_the_hash_chain(self) -> None:
        ledger = AppendOnlyBeliefLedger()
        ledger.append(1, "self", hashlib.sha256(b"t1").hexdigest())
        ledger.append(20, "world", hashlib.sha256(b"t20").hexdigest())

        self.assertTrue(ledger.valid())
        self.assertFalse(ledger.falsified_copy().valid())


if __name__ == "__main__":
    unittest.main()
