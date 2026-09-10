from __future__ import annotations

from pathlib import Path
import sys
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longitudinal.evidence import EvidenceGate, require_evidence
from longitudinal.protocols import all_protocols


class EvidenceLocksTest(TestCase):
    def test_a_failed_lock_rejects_the_result(self) -> None:
        gate = EvidenceGate(True, True, True, True, False, True, True)

        with self.assertRaisesRegex(ValueError, "evidence_locks_failed:sufficiency"):
            require_evidence(gate)

    def test_every_protocol_passes_all_seven_locks(self) -> None:
        for result in all_protocols():
            self.assertTrue(result.gate.accepted(), result.name)
            self.assertEqual(7, len(result.gate.as_dict()))


class LongitudinalProtocolsTest(TestCase):
    def test_all_ten_protocols_have_distinct_identifiers_and_pass(self) -> None:
        results = all_protocols()

        self.assertEqual(tuple(range(1, 11)), tuple(result.test_id for result in results))
        self.assertEqual(10, len({result.name for result in results}))
        for result in results:
            self.assertTrue(result.passed, f"{result.name}: {result.metrics}")

    def test_causal_scar_proves_lesion_and_graft_without_reconstruction(self) -> None:
        result = all_protocols()[0]

        self.assertGreater(result.metrics["lesion_effect"], 0)
        self.assertGreater(result.metrics["graft_effect"], 0)
        self.assertLessEqual(result.metrics["reconstruction_accuracy"], result.metrics["chance_accuracy"])

    def test_identity_follows_history_and_capacity_follows_model(self) -> None:
        result = all_protocols()[1]

        self.assertGreater(result.metrics["cid"], 0)
        self.assertEqual(0.0, result.metrics["model_identity_delta"])
        self.assertGreater(result.metrics["model_capacity_delta"], 0)
        self.assertEqual(0.0, result.metrics["history_capacity_delta"])

    def test_hirt_never_averages_conflicting_values(self) -> None:
        result = all_protocols()[-1]

        self.assertEqual(0.0, result.metrics["unsafe_commit_rate_critical"])
        self.assertEqual(1.0, result.metrics["safe_progress_rate"])
        self.assertFalse(result.metrics["numeric_conflict_averaged"])
