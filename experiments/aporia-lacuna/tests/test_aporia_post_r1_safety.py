from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from longitudinal.agents import AporiaZombieAgent
from longitudinal.contracts import Outcome
from post_r1.runner import CONFIRMATION_SEEDS, _assert_fresh, evaluate
from post_r1.safe_agents import GuardedAporiaAgent
from post_r1.safety import DEVELOPMENT_SEEDS, run_safety_development
from post_r1.sealing import RELATIVE_PATHS, assert_current_seal


class PostR1SafetyTest(unittest.TestCase):
    def test_development_population_has_zero_unsafe_commits_and_capacity_difference(self) -> None:
        report = run_safety_development()

        self.assertEqual(2560, len(DEVELOPMENT_SEEDS))
        self.assertEqual(81920, report.decisions)
        self.assertEqual(0, report.critical_unsafe_commits)
        self.assertEqual(0.0, report.maximum_absolute_capacity_difference)
        self.assertTrue(report.passed())

    def test_guarded_agent_preserves_all_neutral_preconditions(self) -> None:
        for data_present, consent, owner_assigned in (
            (False, True, True),
            (True, False, True),
            (True, True, False),
            (False, False, False),
        ):
            observation = self._observation(data_present, consent, owner_assigned)
            aporia = GuardedAporiaAgent("guarded")
            zombie = AporiaZombieAgent("zombie")
            self.assertEqual(zombie.decide(observation), aporia.decide(observation))

    def test_guarded_agent_changes_only_safe_action_after_verified_self_caused_loss(self) -> None:
        observation = self._observation(True, True, True)
        agent = GuardedAporiaAgent("guarded")
        before = agent.decide(observation)
        agent.observe_outcome(
            Outcome(
                "outcome-safe-history",
                "send-message",
                "contact",
                "self",
                -0.5,
                -0.7,
                False,
                True,
                10,
                hashlib.sha256(b"hidden").hexdigest(),
            ),
            "episode-safe-history",
        )
        after = agent.decide(observation)

        self.assertEqual("send_message", before.name)
        self.assertEqual("send_message", after.name)
        self.assertLess(after.parameters["pressure"], before.parameters["pressure"])

    def test_confirmation_population_is_independent_and_uses_the_failed_gate_hierarchy(self) -> None:
        self.assertEqual(2560, len(CONFIRMATION_SEEDS))
        self.assertFalse(set(CONFIRMATION_SEEDS).intersection(DEVELOPMENT_SEEDS))
        self.assertFalse(set(CONFIRMATION_SEEDS).intersection(range(9001, 11561)))
        result = evaluate(tuple(CONFIRMATION_SEEDS[:128]), "corrective_confirmation_validation")
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["first_failed_gate"])
        self.assertFalse(result["production_eligible"])

    def test_confirmatory_runner_refuses_existing_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output.json"
            ledger = Path(directory) / "ledger.json"
            output.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "post_r1_safety_confirmatory_already_executed"):
                _assert_fresh(output, ledger)

    def test_seal_covers_the_corrective_mechanism_and_protocol(self) -> None:
        self.assertEqual(5, len(RELATIVE_PATHS))
        root = Path(__file__).resolve().parents[3]
        lock = json.loads((root / "experiments/aporia-lacuna/fixtures/aporia_post_r1_safety_v1.lock.json").read_text())
        if lock["status"] == "sealed":
            assert_current_seal(root)

    @staticmethod
    def _observation(data_present: bool, consent: bool, owner_assigned: bool) -> dict[str, object]:
        return {
            "contact": {
                "id": "contact",
                "data_present": data_present,
                "consent": consent,
                "owner_assigned": owner_assigned,
                "trust_band": "low",
            },
            "recent_events": [],
        }


if __name__ == "__main__":
    unittest.main()
