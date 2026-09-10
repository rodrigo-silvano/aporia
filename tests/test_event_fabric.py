"""Unit tests for AporiaEventFabric, perspective projection, and logical LACUNA."""
from __future__ import annotations

import json
import os
import unittest

from aporia.crypto import hmac_sha256_hex, sha256_hex, canonical_json
from aporia.infrastructure.db import Connection
from aporia.infrastructure.event_fabric import AporiaEventFabric
from aporia.infrastructure.lacuna_logical import AporiaLacunaLogical
from aporia.infrastructure.perspective_projector import AporiaPerspectiveProjector
from aporia.infrastructure.autobiography import AporiaAutobiographyLedger
from aporia.infrastructure.identity_continuity import AporiaIdentityLedger


class TestAporiaEventFabric(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = Connection()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._schema()
        self.conn.execute("INSERT INTO assistant_runtime_sessions VALUES (1, 'session-public-a', 49, 'active')")
        self.conn.execute("INSERT INTO assistant_runtime_sessions VALUES (2, 'session-public-b', 49, 'active')")
        self.conn.execute("INSERT INTO assistant_runtime_sessions VALUES (3, 'session-public-c', 73, 'active')")
        self.fabric = AporiaEventFabric(self.conn, "bridge-secret")
        self.lacuna = AporiaLacunaLogical(self.conn, "bridge-secret")
        self.projector = AporiaPerspectiveProjector(self.conn)

    def test_ingestion_is_idempotent_and_rejects_conflicting_replay(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])

        first = self.fabric.ingest(context, event, 1000000)
        second = self.fabric.ingest(context, event, 1000001)

        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_events").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_event_outbox").fetch_column()))

        event["event_kind"] = "tool.proposed"
        event["canonical_sha256"] = self._digest(event)
        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000002)
        self.assertIn("aporia_event_conflict", str(ctx.exception))

    def test_canonical_event_contract_vector(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])

        self.assertEqual("15ddef8ee49741faf8499f1f1c3c4711b7fb4f3d26e274a15dcee2e2bd15235e", event["session_ref"])
        self.assertEqual("470f04b87b85d55464acf236abe4f3956ca4d880d5a71b6d4b05903739565734", event["task_ref"])
        self.assertEqual("db852de96a3b200fab157c995ea2cf2ff020020dc1d9495e6ed7f9a37ccbbfcd", event["canonical_sha256"])

    def test_journal_sequence_cannot_be_reused_with_another_event(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )

        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(
                context,
                self._event(context, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 1, "turn.started", []),
                1000001,
            )
        self.assertIn("aporia_event_sequence_conflict", str(ctx.exception))

    def test_session_predecessor_creates_acyclic_chains_and_separate_sessions_remain_concurrent(self) -> None:
        first_context = self._context(1, 49, "session-public-a", "turn-a")
        second_context = self._context(2, 49, "session-public-b", "turn-b")
        self.fabric.ingest(
            first_context,
            self._event(first_context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.fabric.ingest(
            first_context,
            self._event(first_context, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 2, "turn.completed", [["terminal", True]]),
            1000000,
        )
        self.fabric.ingest(
            second_context,
            self._event(second_context, "5eb59c61-bb97-45e6-9967-a1e3fbc62e48", 1, "turn.started", []),
            1000000,
        )

        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_event_parents").fetch_column()))
        parent = self.conn.query(
            "SELECT parent.event_id FROM aporia_event_parents edge JOIN aporia_events parent ON parent.id = edge.parent_event_id"
        ).fetch_column()
        self.assertEqual("8e766587-0040-43a3-a6ba-8c5689387b38", parent)
        order = self.conn.query(
            "SELECT hlc_wall_us, hlc_logical FROM aporia_events ORDER BY hlc_wall_us, hlc_logical, event_id"
        ).fetch_all()
        self.assertEqual([
            {"hlc_wall_us": 1000000, "hlc_logical": 0},
            {"hlc_wall_us": 1000000, "hlc_logical": 1},
            {"hlc_wall_us": 1000000, "hlc_logical": 2},
        ], [
            {"hlc_wall_us": int(row["hlc_wall_us"]), "hlc_logical": int(row["hlc_logical"])}
            for row in order
        ])
        metrics = self.fabric.metrics(first_context)
        self.assertEqual(3, metrics["event_count"])
        self.assertEqual(1, metrics["rooted_event_count"])
        self.assertEqual(2, metrics["maximum_depth"])
        self.assertAlmostEqual(1 / 3, metrics["causal_coverage"])
        self.assertAlmostEqual(2 / 3, metrics["concurrency_ratio"])

    def test_replay_is_deterministic_and_tenant_scoped(self) -> None:
        tenant_a = self._context(1, 49, "session-public-a", "turn-a")
        tenant_b = self._context(3, 73, "session-public-c", "turn-c")
        self.fabric.ingest(
            tenant_a,
            self._event(tenant_a, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.fabric.ingest(
            tenant_b,
            self._event(tenant_b, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 1, "turn.started", []),
            1000000,
        )

        first = self.fabric.replay(tenant_a, {}, 200)
        second = self.fabric.replay(tenant_a, {}, 200)

        self.assertEqual(first, second)
        self.assertEqual(1, len(first["events"]))
        self.assertEqual("8e766587-0040-43a3-a6ba-8c5689387b38", first["events"][0]["event_id"])
        self.assertNotIn("tenant_id", first["events"][0])
        self.assertNotIn("session_ref", first["events"][0])
        self.assertNotIn("task_ref", first["events"][0])

    def test_tenant_and_opaque_references_are_validated_against_server_context(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])
        event["tenant_id"] = 73

        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("aporia_tenant_forbidden", str(ctx.exception))

        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])
        event["session_ref"] = "a" * 64
        event["canonical_sha256"] = self._digest(event)
        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("aporia_session_forbidden", str(ctx.exception))

    def test_tenant_bound_writes_require_canonical_tenant_context(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])
        del context["tenant_id"]

        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("aporia_tenant_context_required", str(ctx.exception))

        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_events").fetch_column()))

    def test_tenant_bound_writes_reject_owner_session_mismatch(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        context["owner_user_id"] = 73
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])

        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("runtime_session_invalid", str(ctx.exception))

        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_events").fetch_column()))

    def test_forbidden_fields_and_billing_kinds_are_rejected(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        event = self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", [])
        event["stripe"] = "invoice-private"

        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("aporia_event_schema_invalid", str(ctx.exception))

        del event["stripe"]
        event["event_kind"] = "billing.updated"
        event["canonical_sha256"] = self._digest(event)
        with self.assertRaises(RuntimeError) as ctx:
            self.fabric.ingest(context, event, 1000000)
        self.assertEqual("aporia_event_kind_invalid", str(ctx.exception))

    def test_six_perspectives_and_shadow_envelope_are_deterministic_and_non_actionable(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(
                context,
                "8e766587-0040-43a3-a6ba-8c5689387b38",
                1,
                "approval.requested",
                [["approval_required", True]],
            ),
            1000000,
        )

        first = self.projector.project(49, int(result["event_row_id"]))
        envelope = self.projector.envelope(49, int(result["event_row_id"]))
        second = self.projector.project(49, int(result["event_row_id"]))

        self.assertEqual({"completed": True, "deduplicated": False, "observation_count": 6}, first)
        self.assertEqual({"completed": True, "deduplicated": True, "observation_count": 6}, second)
        self.assertEqual(6, int(self.conn.query("SELECT COUNT(*) FROM aporia_perspective_observations").fetch_column()))
        self.assertEqual(6, int(self.conn.query("SELECT COUNT(*) FROM aporia_projection_checkpoints").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT MIN(projection_version) FROM aporia_perspective_observations").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT MAX(projection_version) FROM aporia_projection_checkpoints").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT algorithm_version FROM aporia_lacuna_outbox").fetch_column()))
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_event_outbox").fetch_column())
        self.assertIsNotNone(envelope)
        self.assertEqual("shadow", envelope["delivery_mode"])
        self.assertEqual(6, len(envelope["observation_ids"]))
        self.assertEqual({
            "effect_request": True,
            "policy_mutation": True,
            "prompt_injection": True,
            "tool_injection": True,
        }, envelope["prohibitions"])
        serialized = json.dumps(envelope)
        for forbidden in ["prompt_text", 'tool_injection":false', 'effect_request":false', "reasoning", "stripe", "billing"]:
            self.assertNotIn(forbidden, serialized.lower())
        outcome = self.conn.query(
            "SELECT epistemic_status, uncertainty_codes_json, confidence FROM aporia_perspective_observations WHERE perspective = 'outcome_learning'"
        ).fetch_one()
        self.assertEqual("unknown", outcome["epistemic_status"])
        self.assertEqual(["event_not_outcome"], json.loads(str(outcome["uncertainty_codes_json"])))
        self.assertIsNone(outcome["confidence"])
        causal_json = self.conn.query(
            "SELECT value_json FROM aporia_perspective_observations WHERE perspective = 'causal_continuity'"
        ).fetch_column()
        causal = json.loads(str(causal_json))
        self.assertEqual("root", causal["predecessor_event_kind"])

    def test_projection_cannot_read_another_tenant_event(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )

        with self.assertRaises(RuntimeError) as ctx:
            self.projector.project(73, int(result["event_row_id"]))
        self.assertIn("aporia_projection_event_not_found", str(ctx.exception))

    def test_out_of_order_projection_never_regresses_checkpoints(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        first = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        second = self.fabric.ingest(
            context,
            self._event(context, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 2, "turn.completed", [["terminal", True]]),
            1000000,
        )

        self.projector.project(49, int(second["event_row_id"]))
        self.projector.project(49, int(first["event_row_id"]))

        checkpoints = self.conn.query(
            "SELECT event_id, hlc_logical FROM aporia_projection_checkpoints ORDER BY perspective"
        ).fetch_all()
        self.assertEqual(6, len(checkpoints))
        for checkpoint in checkpoints:
            self.assertEqual("d54e03a3-904f-4f56-a42e-c9eb2867249f", checkpoint["event_id"])
            self.assertEqual(1, int(checkpoint["hlc_logical"]))

    def test_projection_failure_rolls_the_lease_back_to_pending(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.conn.execute("CREATE TRIGGER fail_aporia_envelope BEFORE INSERT ON aporia_shadow_envelopes BEGIN SELECT RAISE(FAIL, 'projection failure'); END")

        try:
            self.projector.project(49, int(result["event_row_id"]))
            self.fail("Expected projection persistence failure.")
        except Exception:
            self.assertEqual("pending", self.conn.query("SELECT status FROM aporia_event_outbox").fetch_column())
            self.assertEqual(1, int(self.conn.query("SELECT attempts FROM aporia_event_outbox").fetch_column()))
            self.assertIsNotNone(self.conn.query("SELECT next_attempt_at FROM aporia_event_outbox").fetch_column())
            self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_perspective_observations").fetch_column()))
            self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_projection_checkpoints").fetch_column()))

        self.conn.execute("DROP TRIGGER fail_aporia_envelope")
        self.conn.execute("UPDATE aporia_event_outbox SET next_attempt_at = '2000-01-01 00:00:00.000000'")
        self.assertTrue(self.projector.process_next())
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_event_outbox").fetch_column())
        self.assertEqual(6, int(self.conn.query("SELECT COUNT(*) FROM aporia_perspective_observations").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_shadow_envelopes").fetch_column()))

    def test_projection_identities_are_tenant_scoped_for_the_same_event_uuid(self) -> None:
        tenant_a = self._context(1, 49, "session-public-a", "turn-a")
        tenant_b = self._context(3, 73, "session-public-c", "turn-c")
        event_id = "8e766587-0040-43a3-a6ba-8c5689387b38"
        first = self.fabric.ingest(tenant_a, self._event(tenant_a, event_id, 1, "turn.started", []), 1000000)
        second = self.fabric.ingest(tenant_b, self._event(tenant_b, event_id, 1, "turn.started", []), 1000000)

        self.projector.project(49, int(first["event_row_id"]))
        self.projector.project(73, int(second["event_row_id"]))

        self.assertEqual(12, int(self.conn.query("SELECT COUNT(*) FROM aporia_perspective_observations").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT COUNT(*) FROM aporia_shadow_envelopes").fetch_column()))
        self.assertEqual(12, int(self.conn.query("SELECT COUNT(DISTINCT observation_id) FROM aporia_perspective_observations").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT COUNT(DISTINCT envelope_id) FROM aporia_shadow_envelopes").fetch_column()))

    def test_logical_lacuna_persists_only_commitments_controls_and_non_claims(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))

        first = self.lacuna.process(49, int(result["event_row_id"]))
        second = self.lacuna.process(49, int(result["event_row_id"]))

        self.assertEqual({"completed": True, "deduplicated": False}, first)
        self.assertEqual({"completed": True, "deduplicated": True}, second)
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_runs").fetch_column()))
        self.assertEqual(2, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_crypto_commits").fetch_column()))
        self.assertEqual(7, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_branches").fetch_column()))
        self.assertEqual(7, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_branches WHERE capability_status = 'consumed'").fetch_column()))
        self.assertEqual(13, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_measurements").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_autobiography_entries").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_negative_autobiography_evaluations").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_identity_snapshots").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_identity_commitment_evidence").fetch_column()))
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())

        order_sensitivity = float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'order_sensitivity'").fetch_column())
        self.assertEqual(0.0, order_sensitivity)
        self.assertEqual("unknown", self.conn.query("SELECT evaluability FROM aporia_lacuna_measurements WHERE metric_name = 'order_sensitivity'").fetch_column())
        self.assertEqual(0.0, float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'passive_control_order'").fetch_column()))
        self.assertEqual(
            float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'lacuna_change_magnitude'").fetch_column()),
            float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'random_control_magnitude'").fetch_column()),
        )
        non_claim = self.conn.query(
            "SELECT numeric_value, evaluability, reason_codes_json FROM aporia_lacuna_measurements WHERE metric_name = 'irrecoverability'"
        ).fetch_one()
        self.assertIsNone(non_claim["numeric_value"])
        self.assertEqual("not_evaluable", non_claim["evaluability"])
        self.assertEqual(["irrecoverability_not_instrumented"], json.loads(str(non_claim["reason_codes_json"])))

        order_evidence = self.conn.query(
            "SELECT numeric_value, evaluability, reason_codes_json FROM aporia_lacuna_measurements WHERE metric_name = 'structural_order_evidence'"
        ).fetch_one()
        self.assertEqual(0.0, float(order_evidence["numeric_value"]))
        self.assertEqual("unknown", order_evidence["evaluability"])
        self.assertEqual(
            ["validated_by_preregistered_v2_corpus", "structural_shadow_only", "not_semantic_causality"],
            json.loads(str(order_evidence["reason_codes_json"])),
        )

        for table in ["aporia_lacuna_runs", "aporia_lacuna_branches", "aporia_lacuna_measurements"]:
            columns = [r["name"] for r in self.conn.query(f"PRAGMA table_info({table})").fetch_all()]
            self.assertNotIn("vector", columns)
            self.assertNotIn("value_json", columns)
            self.assertNotIn("capability_token", columns)

        crypto = self.conn.query(
            """SELECT instrument, input_ciphertext, destroyed_ciphertext, capability_hash, execution_status,
                     process_exit_code, core_dumps_disabled, memory_lock_status, event_name, event_version,
                     stream, category, component, actor_type, action_name, lifecycle_phase, outcome, reason_code
              FROM aporia_lacuna_crypto_commits ORDER BY instrument"""
        ).fetch_all()
        self.assertEqual(["q_then_r_v2", "r_then_q_v2"], [c["instrument"] for c in crypto])
        for commit in crypto:
            self.assertNotEqual(commit["input_ciphertext"], commit["destroyed_ciphertext"])
            self.assertEqual(64, len(str(commit["capability_hash"])))
            self.assertEqual("consumed", commit["execution_status"])
            self.assertEqual(0, int(commit["process_exit_code"]))
            self.assertEqual(1, int(commit["core_dumps_disabled"]))
            self.assertIn(commit["memory_lock_status"], ["locked", "unsupported"])
            self.assertEqual("aporia.lacuna.crypto.consume.succeeded", commit["event_name"])
            self.assertEqual(1, int(commit["event_version"]))
            self.assertEqual("system", commit["stream"])
            self.assertEqual("audit", commit["category"])
            self.assertEqual("aporia-lacuna-sidecar", commit["component"])
            self.assertEqual("worker", commit["actor_type"])
            self.assertEqual("cryptographic_consume", commit["action_name"])
            self.assertEqual("succeeded", commit["lifecycle_phase"])
            self.assertEqual("succeeded", commit["outcome"])
            self.assertIsNone(commit["reason_code"])

        crypto_columns = [r["name"] for r in self.conn.query("PRAGMA table_info(aporia_lacuna_crypto_commits)").fetch_all()]
        for forbidden_column in ["key", "capability_token", "plaintext", "state", "vector"]:
            self.assertNotIn(forbidden_column, crypto_columns)

        autobiography = self.conn.query(
            """SELECT entry_kind, evaluability, ontology_after_commitment, lost_distinction_hashes_json,
                     excluded_future_hashes_json, required_primitive_ids_json, commitment_refs_json,
                     outcome_refs_json, irrecoverability, causal_efficacy, autobiographic_time, confidence,
                     reason_codes_json, event_name, stream, category, actor_type, action_name, outcome, reason_code
              FROM aporia_autobiography_entries"""
        ).fetch_one()
        self.assertEqual("negative_observation", autobiography["entry_kind"])
        self.assertEqual("observed", autobiography["evaluability"])
        for field in ["ontology_after_commitment", "irrecoverability", "causal_efficacy", "autobiographic_time", "confidence"]:
            self.assertIsNone(autobiography[field])
        for field in ["lost_distinction_hashes_json", "excluded_future_hashes_json", "required_primitive_ids_json", "commitment_refs_json", "outcome_refs_json"]:
            self.assertEqual([], json.loads(str(autobiography[field])))
        self.assertEqual(
            ["structural_future_exclusion_observed", "irrecoverability_not_instrumented", "causal_efficacy_not_instrumented", "structural_shadow_only", "content_not_persisted"],
            json.loads(str(autobiography["reason_codes_json"])),
        )
        self.assertEqual("aporia.autobiography.entry.recorded", autobiography["event_name"])
        self.assertEqual("system", autobiography["stream"])
        self.assertEqual("audit", autobiography["category"])
        self.assertEqual("worker", autobiography["actor_type"])
        self.assertEqual("record_negative_observation", autobiography["action_name"])
        self.assertEqual("succeeded", autobiography["outcome"])
        self.assertEqual("negative_autobiography_observed", autobiography["reason_code"])

        self.assertTrue(AporiaAutobiographyLedger(self.conn, "bridge-secret").verify_tenant_chain(49))
        self.assertTrue(AporiaIdentityLedger(self.conn, "bridge-secret").verify_tenant_chain(49))

        negative = self.conn.query(
            """SELECT evaluability, lost_distinction_hashes_json, excluded_future_hashes_json,
                     irrecoverability, causal_efficacy, confidence, event_name, outcome, reason_code
              FROM aporia_negative_autobiography_evaluations"""
        ).fetch_one()
        self.assertEqual("observed", negative["evaluability"])
        self.assertEqual([], json.loads(str(negative["lost_distinction_hashes_json"])))
        self.assertEqual([], json.loads(str(negative["excluded_future_hashes_json"])))
        self.assertIsNone(negative["irrecoverability"])
        self.assertIsNone(negative["causal_efficacy"])
        self.assertIsNone(negative["confidence"])
        self.assertEqual("aporia.autobiography.negative_evaluation.recorded", negative["event_name"])
        self.assertEqual("succeeded", negative["outcome"])
        self.assertEqual("separability_and_future_exclusion_observed", negative["reason_code"])

        identity = self.conn.query(
            "SELECT continuity_score, model_dependency, event_name, actor_type, outcome FROM aporia_identity_snapshots"
        ).fetch_one()
        self.assertEqual(1.0, float(identity["continuity_score"]))
        self.assertEqual("none", identity["model_dependency"])
        self.assertEqual("aporia.identity.snapshot.recorded", identity["event_name"])
        self.assertEqual("worker", identity["actor_type"])
        self.assertEqual("succeeded", identity["outcome"])

        autobiography_columns = [r["name"] for r in self.conn.query("PRAGMA table_info(aporia_autobiography_entries)").fetch_all()]
        for forbidden_column in ["plaintext", "payload", "state", "vector", "token", "password", "reasoning", "prompt"]:
            self.assertNotIn(forbidden_column, autobiography_columns)

    def test_autobiography_chains_are_tenant_scoped_and_detect_tampering(self) -> None:
        tenant_a = self._context(1, 49, "session-public-a", "turn-a")
        tenant_b = self._context(3, 73, "session-public-c", "turn-c")
        events = [
            (tenant_a, "8e766587-0040-43a3-a6ba-8c5689387b38", 1),
            (tenant_a, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 2),
            (tenant_a, "7045db7f-b43c-44fe-b77c-e81c56bfed57", 3),
            (tenant_b, "5eb59c61-bb97-45e6-9967-a1e3fbc62e48", 1),
        ]
        for context, event_id, sequence in events:
            result = self.fabric.ingest(
                context,
                self._event(context, event_id, sequence, "turn.started", []),
                1000000 + sequence,
            )
            tenant_id = int(context["owner_user_id"])
            self.projector.project(tenant_id, int(result["event_row_id"]))
            stmt = self.conn.prepare("UPDATE aporia_shadow_envelopes SET completeness = ? WHERE tenant_id = ? AND event_id = ?")
            stmt.execute(["partial", tenant_id, result["event_row_id"]])
            self.lacuna.process(tenant_id, int(result["event_row_id"]))

        ledger = AporiaAutobiographyLedger(self.conn, "bridge-secret")
        self.assertTrue(ledger.verify_tenant_chain(49))
        self.assertTrue(ledger.verify_tenant_chain(73))
        identity_ledger = AporiaIdentityLedger(self.conn, "bridge-secret")
        self.assertTrue(identity_ledger.verify_tenant_chain(49))
        self.assertTrue(identity_ledger.verify_tenant_chain(73))
        self.assertEqual(3, int(self.conn.query("SELECT entry_count FROM aporia_autobiography_heads WHERE tenant_id = 49").fetch_column()))
        self.assertEqual(1, int(self.conn.query("SELECT entry_count FROM aporia_autobiography_heads WHERE tenant_id = 73").fetch_column()))
        self.assertEqual("committed", self.conn.query("SELECT status FROM aporia_identity_commitments WHERE tenant_id = 49").fetch_column())
        self.assertEqual(3, int(self.conn.query("SELECT evidence_count FROM aporia_identity_commitments WHERE tenant_id = 49").fetch_column()))
        self.assertEqual("proposed", self.conn.query("SELECT status FROM aporia_identity_commitments WHERE tenant_id = 73").fetch_column())
        self.assertGreaterEqual(float(self.conn.query("SELECT MIN(continuity_score) FROM aporia_identity_snapshots WHERE tenant_id = 49").fetch_column()), 0.75)

        self.conn.execute("UPDATE aporia_autobiography_entries SET transformation_commitment = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' WHERE tenant_id = 49 AND id = 1")
        self.assertFalse(ledger.verify_tenant_chain(49))
        self.assertTrue(ledger.verify_tenant_chain(73))

        self.conn.execute("UPDATE aporia_identity_snapshots SET fingerprint = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' WHERE tenant_id = 49 AND id = 1")
        self.assertFalse(identity_ledger.verify_tenant_chain(49))
        self.assertTrue(identity_ledger.verify_tenant_chain(73))

    def test_logical_lacuna_v2_detects_an_allowlisted_predecessor_dependency(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        first = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        second = self.fabric.ingest(
            context,
            self._event(context, "d54e03a3-904f-4f56-a42e-c9eb2867249f", 2, "turn.completed", [["terminal", True]]),
            1000001,
        )
        self.projector.project(49, int(first["event_row_id"]))
        self.projector.project(49, int(second["event_row_id"]))

        causal_json = self.conn.query(
            f"SELECT value_json FROM aporia_perspective_observations WHERE event_id = {int(second['event_row_id'])} AND perspective = 'causal_continuity'"
        ).fetch_column()
        causal = json.loads(str(causal_json))
        self.assertEqual("turn.started", causal["predecessor_event_kind"])

        self.lacuna.process(49, int(second["event_row_id"]))

        run = self.conn.query(
            "SELECT algorithm_version, feature_space_version FROM aporia_lacuna_runs ORDER BY id DESC LIMIT 1"
        ).fetch_one()
        self.assertEqual(2, int(run["algorithm_version"]))
        self.assertEqual(2, int(run["feature_space_version"]))
        order_sensitivity = float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'order_sensitivity'").fetch_column())
        structural_evidence = float(self.conn.query("SELECT numeric_value FROM aporia_lacuna_measurements WHERE metric_name = 'structural_order_evidence'").fetch_column())
        self.assertGreater(order_sensitivity, 0.0)
        self.assertEqual(order_sensitivity, structural_evidence)

    def test_logical_lacuna_rejects_cross_tenant_access_and_capability_replay(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        with self.assertRaises(RuntimeError) as ctx:
            self.lacuna.process(73, int(result["event_row_id"]))
        self.assertIn("aporia_lacuna_source_not_found", str(ctx.exception))

        self.lacuna.process(49, int(result["event_row_id"]))
        branch_id = str(self.conn.query("SELECT branch_id FROM aporia_lacuna_branches ORDER BY id LIMIT 1").fetch_column())
        token = "one-shot-test-token"
        capability_hash = hmac_sha256_hex("bridge-secret", f"49|{branch_id}|{token}")
        stmt = self.conn.prepare("UPDATE aporia_lacuna_branches SET capability_hash = ?, capability_status = 'ready' WHERE branch_id = ?")
        stmt.execute([capability_hash, branch_id])

        self.lacuna.consume_capability(49, branch_id, token)
        with self.assertRaises(RuntimeError) as ctx:
            self.lacuna.consume_capability(49, branch_id, token)
        self.assertIn("aporia_lacuna_capability_consumed", str(ctx.exception))

    def test_logical_lacuna_marks_partial_envelope_not_evaluable_without_zero_scores(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        self.conn.execute("UPDATE aporia_shadow_envelopes SET completeness = 'partial'")

        self.lacuna.process(49, int(result["event_row_id"]))

        self.assertEqual("not_evaluable", self.conn.query("SELECT interpretation_status FROM aporia_lacuna_runs").fetch_column())
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_branches").fetch_column()))
        self.assertEqual(6, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_measurements").fetch_column()))
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(numeric_value) FROM aporia_lacuna_measurements").fetch_column()))

    def test_logical_lacuna_outbox_recovers_after_transactional_failure(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        self.conn.execute("CREATE TRIGGER fail_lacuna_run BEFORE INSERT ON aporia_lacuna_runs BEGIN SELECT RAISE(FAIL, 'lacuna failure'); END")

        try:
            self.lacuna.process(49, int(result["event_row_id"]))
            self.fail("Expected LACUNA persistence failure.")
        except Exception:
            self.assertEqual("pending", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
            self.assertEqual(1, int(self.conn.query("SELECT attempts FROM aporia_lacuna_outbox").fetch_column()))
            self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_runs").fetch_column()))

        self.conn.execute("DROP TRIGGER fail_lacuna_run")
        self.conn.execute("UPDATE aporia_lacuna_outbox SET next_attempt_at = '2000-01-01 00:00:00.000000'")
        self.assertTrue(self.lacuna.process_next())
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_runs").fetch_column()))

    def test_autobiography_failure_rolls_back_the_entire_lacuna_transaction(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        self.conn.execute("CREATE TRIGGER fail_autobiography BEFORE INSERT ON aporia_autobiography_entries BEGIN SELECT RAISE(FAIL, 'autobiography failure'); END")

        try:
            self.lacuna.process(49, int(result["event_row_id"]))
            self.fail("Expected autobiography persistence failure.")
        except Exception:
            self.assertEqual("pending", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
            self.assertEqual(1, int(self.conn.query("SELECT attempts FROM aporia_lacuna_outbox").fetch_column()))
            for table in ["aporia_lacuna_runs", "aporia_lacuna_branches", "aporia_lacuna_measurements", "aporia_lacuna_crypto_commits", "aporia_autobiography_entries", "aporia_autobiography_heads"]:
                self.assertEqual(0, int(self.conn.query(f"SELECT COUNT(*) FROM {table}").fetch_column()))

        self.conn.execute("DROP TRIGGER fail_autobiography")
        self.conn.execute("UPDATE aporia_lacuna_outbox SET next_attempt_at = '2000-01-01 00:00:00.000000'")
        self.assertTrue(self.lacuna.process_next())
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_autobiography_entries").fetch_column()))
        self.assertTrue(AporiaAutobiographyLedger(self.conn, "bridge-secret").verify_tenant_chain(49))

    def test_identity_failure_rolls_back_every_lacuna_and_autobiography_write(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        self.conn.execute("CREATE TRIGGER fail_identity_snapshot BEFORE INSERT ON aporia_identity_snapshots BEGIN SELECT RAISE(FAIL, 'identity failure'); END")

        try:
            self.lacuna.process(49, int(result["event_row_id"]))
            self.fail("Expected identity persistence failure.")
        except Exception:
            self.assertEqual("pending", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
            self.assertEqual(1, int(self.conn.query("SELECT attempts FROM aporia_lacuna_outbox").fetch_column()))
            for table in [
                "aporia_lacuna_runs",
                "aporia_lacuna_branches",
                "aporia_lacuna_measurements",
                "aporia_lacuna_crypto_commits",
                "aporia_autobiography_entries",
                "aporia_autobiography_heads",
                "aporia_negative_autobiography_evaluations",
                "aporia_identity_snapshots",
                "aporia_identity_heads",
                "aporia_identity_commitments",
                "aporia_identity_commitment_evidence",
            ]:
                self.assertEqual(0, int(self.conn.query(f"SELECT COUNT(*) FROM {table}").fetch_column()))

        self.conn.execute("DROP TRIGGER fail_identity_snapshot")
        self.conn.execute("UPDATE aporia_lacuna_outbox SET next_attempt_at = '2000-01-01 00:00:00.000000'")
        self.assertTrue(self.lacuna.process_next())
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
        self.assertTrue(AporiaIdentityLedger(self.conn, "bridge-secret").verify_tenant_chain(49))

    def test_ontology_residual_failure_rolls_back_the_whole_runtime_observation(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        self.conn.execute("CREATE TRIGGER fail_ontology_residual BEFORE INSERT ON aporia_ontology_residuals BEGIN SELECT RAISE(FAIL, 'ontology residual failure'); END")

        try:
            self.lacuna.process(49, int(result["event_row_id"]))
            self.fail("Expected ontology residual persistence failure.")
        except Exception:
            self.assertEqual("pending", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
            for table in [
                "aporia_lacuna_runs",
                "aporia_lacuna_branches",
                "aporia_lacuna_measurements",
                "aporia_lacuna_crypto_commits",
                "aporia_autobiography_entries",
                "aporia_autobiography_heads",
                "aporia_negative_autobiography_evaluations",
                "aporia_ontology_residuals",
                "aporia_ontology_clusters",
                "aporia_ontology_versions",
                "aporia_ontology_primitives",
            ]:
                self.assertEqual(0, int(self.conn.query(f"SELECT COUNT(*) FROM {table}").fetch_column()))

        self.conn.execute("DROP TRIGGER fail_ontology_residual")
        self.conn.execute("UPDATE aporia_lacuna_outbox SET next_attempt_at = '2000-01-01 00:00:00.000000'")
        self.assertTrue(self.lacuna.process_next())
        self.assertEqual("completed", self.conn.query("SELECT status FROM aporia_lacuna_outbox").fetch_column())
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_ontology_residuals").fetch_column()))

    def test_cryptographic_sidecar_failure_rolls_back_and_persists_a_safe_reason_code(self) -> None:
        context = self._context(1, 49, "session-public-a", "turn-a")
        result = self.fabric.ingest(
            context,
            self._event(context, "8e766587-0040-43a3-a6ba-8c5689387b38", 1, "turn.started", []),
            1000000,
        )
        self.projector.project(49, int(result["event_row_id"]))
        fixture_path = os.path.join(os.path.dirname(__file__), "Fixtures", "aporia_invalid_sidecar.py")
        lacuna = AporiaLacunaLogical(
            self.conn,
            "bridge-secret",
            fixture_path,
        )

        with self.assertRaises(RuntimeError) as ctx:
            lacuna.process(49, int(result["event_row_id"]))
        self.assertEqual("aporia_crypto_handshake_invalid", str(ctx.exception))

        outbox = self.conn.query(
            "SELECT status, attempts, reason_code FROM aporia_lacuna_outbox"
        ).fetch_one()
        self.assertEqual("pending", outbox["status"])
        self.assertEqual(1, int(outbox["attempts"]))
        self.assertEqual("aporia_crypto_handshake_invalid", outbox["reason_code"])
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_runs").fetch_column()))
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_lacuna_crypto_commits").fetch_column()))

    def _context(self, session_id: int, owner: int, public_id: str, turn_id: str) -> dict:
        return {
            "id": session_id,
            "public_id": public_id,
            "tenant_id": owner,
            "owner_user_id": owner,
            "actor_user_id": owner,
            "membership_id": 1000 + session_id,
            "role": "owner",
            "privacy_scope": "TENANT_PRIVATE",
            "context_revision": 1,
            "tenant_context_status": "resolved",
            "tenant_context_source": "canonical",
            "turn_id": turn_id,
        }

    def _event(self, context: dict, event_id: str, sequence: int, kind: str, attributes: list) -> dict:
        tenant_id = int(context["tenant_id"])
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "tenant_id": tenant_id,
            "session_ref": hmac_sha256_hex("bridge-secret", f"aporia:{tenant_id}:session:{context['public_id']}"),
            "task_ref": hmac_sha256_hex("bridge-secret", f"aporia:{tenant_id}:task:{context['turn_id']}"),
            "event_kind": kind,
            "source_service": "agent-gateway",
            "occurred_at": "2026-08-16T00:00:00+00:00",
            "journal_sequence": sequence,
            "attributes": attributes,
            "completeness": "complete",
        }
        event["canonical_sha256"] = self._digest(event)
        return event

    def _digest(self, event: dict) -> str:
        copy_ev = {k: v for k, v in event.items() if k != "canonical_sha256"}
        return sha256_hex(canonical_json(copy_ev).encode("utf-8"))

    def _schema(self) -> None:
        self.conn.execute("CREATE TABLE assistant_runtime_sessions (id INTEGER PRIMARY KEY, public_id TEXT NOT NULL, owner_user_id INTEGER NOT NULL, status TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_tenant_clocks (tenant_id INTEGER PRIMARY KEY, hlc_wall_us INTEGER NOT NULL, hlc_logical INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, runtime_session_id INTEGER NOT NULL, session_ref TEXT NOT NULL, task_ref TEXT, event_kind TEXT NOT NULL, source_service TEXT NOT NULL, occurred_at TEXT NOT NULL, journal_sequence INTEGER NOT NULL, ingested_at TEXT NOT NULL, hlc_wall_us INTEGER NOT NULL, hlc_logical INTEGER NOT NULL, causal_depth INTEGER NOT NULL, attributes_json TEXT NOT NULL, canonical_sha256 TEXT NOT NULL, completeness TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, event_id), UNIQUE (tenant_id, source_service, event_id), UNIQUE (tenant_id, session_ref, journal_sequence))")
        self.conn.execute("CREATE TABLE aporia_event_parents (tenant_id INTEGER NOT NULL, child_event_id INTEGER NOT NULL, parent_event_id INTEGER NOT NULL, relation_type TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (child_event_id, parent_event_id))")
        self.conn.execute("CREATE TABLE aporia_event_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, event_id INTEGER NOT NULL UNIQUE, delivery_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, fencing_token INTEGER NOT NULL, owner_token_hash TEXT, attempts INTEGER NOT NULL, lease_expires_at TEXT, next_attempt_at TEXT, completed_at TEXT, reason_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_perspective_observations (id INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, event_id INTEGER NOT NULL, perspective TEXT NOT NULL, subject_ref TEXT NOT NULL, epistemic_status TEXT NOT NULL, confidence REAL, uncertainty_codes_json TEXT NOT NULL, evidence_event_ids_json TEXT NOT NULL, value_json TEXT NOT NULL, projection_version INTEGER NOT NULL, adapter_version INTEGER NOT NULL, policy_hash TEXT NOT NULL, input_event_set_hash TEXT NOT NULL, produced_at TEXT NOT NULL, valid_until TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, event_id, perspective, projection_version))")
        self.conn.execute("CREATE TABLE aporia_shadow_envelopes (id INTEGER PRIMARY KEY AUTOINCREMENT, envelope_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, event_id INTEGER NOT NULL, session_ref TEXT NOT NULL, task_ref TEXT, delivery_mode TEXT NOT NULL, observation_ids_json TEXT NOT NULL, event_count INTEGER NOT NULL, omitted_event_count INTEGER NOT NULL, completeness TEXT NOT NULL, source_hlc_wall_us INTEGER NOT NULL, source_hlc_logical INTEGER NOT NULL, prohibitions_json TEXT NOT NULL, policy_hash TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, event_id))")
        self.conn.execute("CREATE TABLE aporia_projection_checkpoints (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, perspective TEXT NOT NULL, projection_version INTEGER NOT NULL, hlc_wall_us INTEGER NOT NULL, hlc_logical INTEGER NOT NULL, event_id TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, perspective, projection_version))")
        self.conn.execute("CREATE TABLE aporia_lacuna_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, source_event_id INTEGER NOT NULL, envelope_id TEXT NOT NULL, algorithm_version INTEGER NOT NULL, delivery_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, fencing_token INTEGER NOT NULL, owner_token_hash TEXT, attempts INTEGER NOT NULL, lease_expires_at TEXT, next_attempt_at TEXT, completed_at TEXT, reason_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, source_event_id, algorithm_version), UNIQUE (tenant_id, envelope_id, algorithm_version))")
        self.conn.execute("CREATE TABLE aporia_lacuna_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, source_event_id INTEGER NOT NULL, envelope_id TEXT NOT NULL, algorithm_version INTEGER NOT NULL, feature_space_version INTEGER NOT NULL, policy_hash TEXT NOT NULL, input_commitment TEXT NOT NULL, control_seed TEXT NOT NULL, execution_status TEXT NOT NULL, interpretation_status TEXT NOT NULL, reason_codes_json TEXT NOT NULL, completed_at TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, source_event_id, algorithm_version), UNIQUE (tenant_id, id))")
        self.conn.execute("CREATE TABLE aporia_lacuna_crypto_commits (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, latent_id TEXT NOT NULL UNIQUE, instrument TEXT NOT NULL, input_ciphertext BLOB NOT NULL, input_nonce TEXT NOT NULL, destroyed_ciphertext BLOB NOT NULL, destroyed_nonce TEXT NOT NULL, input_commitment TEXT NOT NULL, output_commitment TEXT NOT NULL, capability_hash TEXT NOT NULL UNIQUE, destruction_receipt TEXT NOT NULL, execution_status TEXT NOT NULL, process_exit_code INTEGER NOT NULL, core_dumps_disabled INTEGER NOT NULL, memory_lock_status TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT, trace_id TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, run_id, instrument))")
        self.conn.execute("CREATE TABLE aporia_lacuna_branches (id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, branch_name TEXT NOT NULL, control_kind TEXT NOT NULL, instrument_order_json TEXT NOT NULL, capability_hash TEXT NOT NULL, capability_status TEXT NOT NULL, input_commitment TEXT NOT NULL, output_commitment TEXT NOT NULL, changed_dimensions INTEGER NOT NULL, targeted_dimensions INTEGER NOT NULL, operation_index INTEGER NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, run_id, branch_name), UNIQUE (tenant_id, run_id, operation_index))")
        self.conn.execute("CREATE TABLE aporia_lacuna_measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, metric_name TEXT NOT NULL, metric_version INTEGER NOT NULL, numeric_value REAL, unit TEXT NOT NULL, evaluability TEXT NOT NULL, reason_codes_json TEXT NOT NULL, baseline_branch_ids_json TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, run_id, metric_name, metric_version))")
        self.conn.execute("CREATE TABLE aporia_autobiography_heads (tenant_id INTEGER PRIMARY KEY, last_signature TEXT NOT NULL, entry_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_autobiography_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, causal_event_id TEXT NOT NULL, episode_ref TEXT NOT NULL, entry_kind TEXT NOT NULL, evaluability TEXT NOT NULL, ontology_before_commitment TEXT NOT NULL, ontology_after_commitment TEXT, transformation_commitment TEXT NOT NULL, lost_distinction_hashes_json TEXT NOT NULL, excluded_future_hashes_json TEXT NOT NULL, required_primitive_ids_json TEXT NOT NULL, commitment_refs_json TEXT NOT NULL, outcome_refs_json TEXT NOT NULL, irrecoverability REAL, causal_efficacy REAL, autobiographic_time INTEGER, confidence REAL, reason_codes_json TEXT NOT NULL, policy_hash TEXT NOT NULL, payload_hash TEXT NOT NULL, previous_signature TEXT NOT NULL, entry_signature TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, entry_id), UNIQUE (tenant_id, run_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_negative_autobiography_evaluations (id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, autobiography_entry_id TEXT NOT NULL, causal_event_id TEXT NOT NULL, evaluability TEXT NOT NULL, lost_distinction_hashes_json TEXT NOT NULL, excluded_future_hashes_json TEXT NOT NULL, distinction_loss_rate REAL, future_exclusion_rate REAL, irrecoverability REAL, causal_efficacy REAL, confidence REAL, reason_codes_json TEXT NOT NULL, delta_high REAL NOT NULL, delta_low REAL NOT NULL, payload_hash TEXT NOT NULL, evaluation_signature TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, evaluation_id), UNIQUE (tenant_id, autobiography_entry_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_identity_heads (tenant_id INTEGER PRIMARY KEY, last_signature TEXT NOT NULL, snapshot_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_identity_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, autobiography_entry_id TEXT NOT NULL, graph_commitments_json TEXT NOT NULL, behavior_commitments_json TEXT NOT NULL, commitment_commitments_json TEXT NOT NULL, autobiography_commitments_json TEXT NOT NULL, ontology_commitments_json TEXT NOT NULL, graph_root TEXT NOT NULL, behavior_root TEXT NOT NULL, commitment_root TEXT NOT NULL, autobiography_root TEXT NOT NULL, ontology_root TEXT NOT NULL, graph_count INTEGER NOT NULL, behavior_count INTEGER NOT NULL, commitment_count INTEGER NOT NULL, autobiography_count INTEGER NOT NULL, ontology_count INTEGER NOT NULL, fingerprint TEXT NOT NULL, continuity_score REAL NOT NULL, model_dependency TEXT NOT NULL, previous_signature TEXT NOT NULL, snapshot_signature TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, snapshot_id), UNIQUE (tenant_id, autobiography_entry_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_identity_commitments (id INTEGER PRIMARY KEY AUTOINCREMENT, commitment_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, commitment_key TEXT NOT NULL, commitment_kind TEXT NOT NULL, status TEXT NOT NULL, evidence_count INTEGER NOT NULL, first_causal_event_id TEXT NOT NULL, last_causal_event_id TEXT NOT NULL, committed_at TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, commitment_id), UNIQUE (tenant_id, commitment_key), UNIQUE (tenant_id, id))")
        self.conn.execute("CREATE TABLE aporia_identity_commitment_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, commitment_id INTEGER NOT NULL, autobiography_entry_id TEXT NOT NULL, causal_event_id TEXT NOT NULL, evidence_ordinal INTEGER NOT NULL, resulting_status TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, evidence_id), UNIQUE (tenant_id, commitment_id, autobiography_entry_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_ontology_heads (tenant_id INTEGER PRIMARY KEY, current_version_id TEXT, version_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE aporia_ontology_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_number INTEGER NOT NULL, parent_version_id TEXT, status TEXT NOT NULL, version_commitment TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT, UNIQUE (tenant_id, version_id), UNIQUE (tenant_id, version_number))")
        self.conn.execute("CREATE TABLE aporia_ontology_primitives (id INTEGER PRIMARY KEY AUTOINCREMENT, primitive_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_id TEXT NOT NULL, definition_commitment TEXT NOT NULL, positive_example_commitments_json TEXT NOT NULL, negative_example_commitments_json TEXT NOT NULL, vector_json TEXT NOT NULL, state TEXT NOT NULL, duplicate_similarity REAL NOT NULL, mdl_before REAL NOT NULL, mdl_after REAL NOT NULL, mdl_improvement REAL NOT NULL, holdout_improvement REAL NOT NULL, bootstrap_lower_95 REAL NOT NULL, topology_before_json TEXT, topology_after_json TEXT NOT NULL, topology_change REAL, automatic_activation_allowed INTEGER NOT NULL, reason_codes_json TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, primitive_id), UNIQUE (tenant_id, version_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_ontology_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ontology_event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_id TEXT NOT NULL, target_version_id TEXT, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, ontology_event_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_ontology_clusters (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, cluster_key TEXT NOT NULL, status TEXT NOT NULL, train_count INTEGER NOT NULL, holdout_count INTEGER NOT NULL, distinct_episode_count INTEGER NOT NULL, last_evaluated_train_count INTEGER NOT NULL, last_evaluated_holdout_count INTEGER NOT NULL, proposed_version_id TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, cluster_id), UNIQUE (tenant_id, cluster_key))")
        self.conn.execute("CREATE TABLE aporia_ontology_residuals (id INTEGER PRIMARY KEY AUTOINCREMENT, residual_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, cluster_key TEXT NOT NULL, dataset_split TEXT NOT NULL, residual_vector_json TEXT NOT NULL, residual_commitment TEXT NOT NULL, baseline_loss REAL NOT NULL, nearest_similarity REAL NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, residual_id), UNIQUE (tenant_id, run_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_obstruction_measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, measurement_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, world_score REAL NOT NULL, self_score REAL NOT NULL, corrected_self_score REAL NOT NULL, cycle_score REAL NOT NULL, classification TEXT NOT NULL, satisfiable INTEGER NOT NULL, self_referential INTEGER NOT NULL, unsat_constraint_hashes_json TEXT NOT NULL, multi_start_scores_json TEXT NOT NULL, reason_codes_json TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, measurement_id), UNIQUE (tenant_id, run_id), UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_experiment_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, experiment_key TEXT NOT NULL, arm TEXT NOT NULL, seed INTEGER NOT NULL, assignment_commitment TEXT NOT NULL, assigned_before_outcome INTEGER NOT NULL, created_at TEXT, updated_at TEXT, UNIQUE (tenant_id, assignment_id), UNIQUE (tenant_id, episode_ref, experiment_key))")
        self.conn.execute("CREATE TABLE aporia_experiment_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, outcome_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, assignment_id TEXT NOT NULL, run_id INTEGER NOT NULL, metric_name TEXT NOT NULL, numeric_value REAL NOT NULL, favorable_direction TEXT NOT NULL, reason_codes_json TEXT NOT NULL, UNIQUE (tenant_id, outcome_id), UNIQUE (tenant_id, run_id, metric_name))")


if __name__ == "__main__":
    unittest.main()
