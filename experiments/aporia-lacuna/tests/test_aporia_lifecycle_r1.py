from __future__ import annotations

import hashlib
import unittest

from replication_r1.lifecycle import EpisodeLifecycle, LifecycleEvent, run_chaos
from replication_r1.poisoning import PersistentInputGuard, poisoning_matrix


class ShadowLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.turn_ref = hashlib.sha256(b"turn").hexdigest()
        self.lifecycle = EpisodeLifecycle(49, self.turn_ref)
        for event in self._prefix():
            self.lifecycle.apply(event)

    def test_usage_and_outcome_can_arrive_in_either_order_without_early_revocation(self) -> None:
        outcome = hashlib.sha256(b"outcome").hexdigest()
        self.lifecycle.apply(LifecycleEvent("usage", 49, self.turn_ref, "usage_terminal"))
        self.lifecycle.apply(LifecycleEvent("outcome", 49, self.turn_ref, "outcome_terminal", payload_commitment=outcome))
        self.lifecycle.apply(LifecycleEvent("archive", 49, self.turn_ref, "archived"))
        self.assertFalse(self.lifecycle.can_revoke())
        self.lifecycle.apply(LifecycleEvent("done", 49, self.turn_ref, "turn_done"))
        self.assertTrue(self.lifecycle.can_revoke())
        self.assertTrue(self.lifecycle.apply(LifecycleEvent("revoke", 49, self.turn_ref, "revoke")))
        self.assertFalse(self.lifecycle.apply(LifecycleEvent("revoke-second", 49, self.turn_ref, "revoke")))

    def test_timeout_marks_missing_terminal_states_without_faking_completion(self) -> None:
        self.lifecycle.apply(LifecycleEvent("archive", 49, self.turn_ref, "archived"))
        self.lifecycle.apply(LifecycleEvent("timeout", 49, self.turn_ref, "timeout"))
        self.assertEqual({"turn_done", "usage_terminal", "outcome_terminal"}, self.lifecycle.missing)
        self.assertFalse({"turn_done", "usage_terminal", "outcome_terminal"}.intersection(self.lifecycle.milestones))
        self.assertTrue(self.lifecycle.can_revoke())
        with self.assertRaisesRegex(RuntimeError, "lifecycle_missing_is_terminal"):
            self.lifecycle.apply(LifecycleEvent("late-outcome", 49, self.turn_ref, "outcome_terminal", payload_commitment="f" * 64))

    def test_worker_restart_restores_idempotency_and_causal_audit_trail(self) -> None:
        restored = EpisodeLifecycle.restore(self.lifecycle.snapshot())
        outcome = hashlib.sha256(b"outcome").hexdigest()
        event = LifecycleEvent("outcome", 49, self.turn_ref, "outcome_terminal", payload_commitment=outcome)
        self.assertTrue(restored.apply(event))
        self.assertFalse(restored.apply(event))
        audit = restored.audit_events[-1]
        self.assertEqual("aporia.shadow.lifecycle.outcome_terminal", audit["event_name"])
        self.assertEqual("system", audit["stream"])
        self.assertEqual("audit", audit["category"])
        self.assertNotIn("prompt", audit)
        self.assertNotIn("payload", audit)

    def test_cross_tenant_wrong_turn_and_conflicting_outcome_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "lifecycle_scope_mismatch"):
            self.lifecycle.apply(LifecycleEvent("foreign", 50, self.turn_ref, "turn_done"))
        with self.assertRaisesRegex(ValueError, "lifecycle_scope_mismatch"):
            self.lifecycle.apply(LifecycleEvent("wrong", 49, "0" * 64, "turn_done"))
        first = hashlib.sha256(b"first").hexdigest()
        second = hashlib.sha256(b"second").hexdigest()
        self.lifecycle.apply(LifecycleEvent("first", 49, self.turn_ref, "outcome_terminal", payload_commitment=first))
        with self.assertRaisesRegex(ValueError, "lifecycle_outcome_conflict"):
            self.lifecycle.apply(LifecycleEvent("second", 49, self.turn_ref, "outcome_terminal", payload_commitment=second))

    def test_two_simultaneous_turns_remain_independent(self) -> None:
        other_ref = hashlib.sha256(b"other").hexdigest()
        other = EpisodeLifecycle(49, other_ref)
        for index, event in enumerate(self._prefix()):
            other.apply(LifecycleEvent(f"other-{index}", 49, other_ref, event.kind, event.arm))
        self.assertNotEqual(self.lifecycle.turn_ref, other.turn_ref)
        self.assertEqual({"C0", "C5"}, self.lifecycle.prediction_arms)
        self.assertEqual({"C0", "C5"}, other.prediction_arms)

    def test_one_hundred_thousand_chaos_sequences_preserve_all_gates(self) -> None:
        report = run_chaos()
        self.assertTrue(report.passed())
        self.assertGreater(report.safe_rejections, 0)
        self.assertEqual(14, len(report.scenario_coverage))
        self.assertTrue(all(count > 0 for count in report.scenario_coverage.values()))

    def _prefix(self) -> tuple[LifecycleEvent, ...]:
        return (
            LifecycleEvent("assigned", 49, self.turn_ref, "assigned"),
            LifecycleEvent("preparing", 49, self.turn_ref, "preparing"),
            LifecycleEvent("prepared", 49, self.turn_ref, "prepared"),
            LifecycleEvent("exposed", 49, self.turn_ref, "exposed"),
            LifecycleEvent("prediction-c0", 49, self.turn_ref, "prediction_sealed", "C0"),
            LifecycleEvent("prediction-c5", 49, self.turn_ref, "prediction_sealed", "C5"),
        )


class LongitudinalPoisoningTest(unittest.TestCase):
    def test_all_eight_persistent_poisoning_classes_are_rejected(self) -> None:
        decisions = poisoning_matrix()
        self.assertEqual(8, len(decisions))
        self.assertTrue(all(not item.accepted for item in decisions.values()))
        self.assertTrue(all(item.persisted_fields == () for item in decisions.values()))

    def test_verified_commitment_persists_only_allowlisted_non_content_fields(self) -> None:
        guard = PersistentInputGuard(49, "lineage-49", "poisoning-secret")
        parent = hashlib.sha256(b"parent").hexdigest()
        candidate = guard.issue("verified_transition", parent, "verified_outcome", "private synthetic content")
        decision = guard.inspect(candidate)
        self.assertTrue(decision.accepted)
        self.assertNotIn("content", decision.persisted_fields)
        self.assertNotIn("signature", decision.persisted_fields)

    def test_cross_tenant_identity_and_forged_signature_are_rejected(self) -> None:
        guard = PersistentInputGuard(49, "lineage-49", "poisoning-secret")
        parent = hashlib.sha256(b"parent").hexdigest()
        candidate = guard.issue("verified_transition", parent, "verified_outcome", "content")
        foreign = candidate.__class__(**{**candidate.__dict__, "tenant_id": 50})
        forged = candidate.__class__(**{**candidate.__dict__, "signature": "0" * 64})
        self.assertFalse(guard.inspect(foreign).accepted)
        self.assertFalse(guard.inspect(forged).accepted)


if __name__ == "__main__":
    unittest.main()
