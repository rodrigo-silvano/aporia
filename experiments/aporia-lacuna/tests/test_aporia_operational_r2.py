from __future__ import annotations

import json
import unittest
from pathlib import Path

from operational_r2.chaos import SCENARIOS, run_chaos_r2
from operational_r2.lifecycle import EpisodeLifecycleR2, event
from operational_r2.poisoning import run_poisoning_r2
from operational_r2.runner import CONFIRMATION_SEED, DEVELOPMENT_SEED
from operational_r2.sealing import RELATIVE_PATHS, assert_current_seal, code_commitment


ROOT = Path(__file__).resolve().parents[3]


class OperationalR2Test(unittest.TestCase):
    def test_one_hundred_thousand_semantic_scenarios_pass_all_gates(self) -> None:
        report = run_chaos_r2()

        self.assertTrue(report.passed())
        self.assertEqual(100000, report.sequences)
        self.assertEqual(set(SCENARIOS), set(report.scenario_coverage))
        self.assertTrue(all(count > 0 for count in report.scenario_coverage.values()))
        self.assertGreater(report.safe_rejections, 0)
        self.assertGreater(report.fault_recoveries, 0)

    def test_revocation_requires_archive_and_every_terminal_state(self) -> None:
        lifecycle = EpisodeLifecycleR2(49, "a" * 64)
        for item in (
            event("assigned", 49, lifecycle.turn_ref, "assigned"),
            event("preparing", 49, lifecycle.turn_ref, "preparing"),
            event("prepared", 49, lifecycle.turn_ref, "prepared"),
            event("exposed", 49, lifecycle.turn_ref, "exposed"),
            event("c0", 49, lifecycle.turn_ref, "prediction_sealed", "C0"),
            event("c5", 49, lifecycle.turn_ref, "prediction_sealed", "C5"),
            event("usage", 49, lifecycle.turn_ref, "usage_terminal"),
            event("outcome", 49, lifecycle.turn_ref, "outcome_terminal", payload="f" * 64),
            event("archive", 49, lifecycle.turn_ref, "archived"),
        ):
            lifecycle.apply(item)

        self.assertFalse(lifecycle.can_revoke())
        lifecycle.apply(event("turn", 49, lifecycle.turn_ref, "turn_done"))
        self.assertTrue(lifecycle.can_revoke())
        self.assertTrue(lifecycle.apply(event("revoke", 49, lifecycle.turn_ref, "revoked")))
        self.assertFalse(lifecycle.apply(event("revoke-again", 49, lifecycle.turn_ref, "revoked")))

    def test_all_eight_poisoning_classes_remain_absent_across_sessions_and_tenants(self) -> None:
        report = run_poisoning_r2()

        self.assertTrue(report.passed())
        self.assertEqual(8, report.attack_classes)
        self.assertEqual(2048, report.attack_attempts)
        self.assertEqual(report.attack_attempts, report.rejected_attempts)
        self.assertEqual(0, report.cross_session_reappearances)
        self.assertEqual(0, report.cross_tenant_contamination)
        self.assertEqual(0, report.raw_content_fields)

    def test_confirmation_seed_is_independent(self) -> None:
        self.assertNotEqual(DEVELOPMENT_SEED, CONFIRMATION_SEED)

    def test_seal_covers_every_outcome_affecting_file(self) -> None:
        assert_current_seal(ROOT)
        lock = json.loads((ROOT / "experiments/aporia-lacuna/fixtures/aporia_operational_r2.lock.json").read_text())

        self.assertEqual(code_commitment(ROOT), lock["code_commitment"])
        self.assertEqual(len(RELATIVE_PATHS), lock["sealed_file_count"])
        self.assertFalse(lock["changes_after_sealing_allowed"])


if __name__ == "__main__":
    unittest.main()
