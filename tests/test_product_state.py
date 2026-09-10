"""Unit tests for Aporia Product State, Context Service, Lacuna Gateway, Operational Metrics, and Advanced Phases."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import unittest

from aporia.application.product_context import AporiaProductContextService
from aporia.config import Environment
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.infrastructure.db import Connection
from aporia.infrastructure.effect_compiler import AporiaEffectCompiler
from aporia.infrastructure.experiments import AporiaScientificExperiment
from aporia.infrastructure.guarded_policy import AporiaGuardedPolicy
from aporia.infrastructure.hirt_gateway import AporiaHirtGateway, AporiaProductHirtGateway
from aporia.infrastructure.obstruction_engine import AporiaObstructionEngine
from aporia.infrastructure.product_lacuna_gateway import AporiaProductLacunaGateway
from aporia.infrastructure.product_state import (
    AporiaProductOperationalMetrics,
    AporiaProductStateRepository,
)


class TestAporiaProductState(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = Connection()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE aporia_product_state_items (id INTEGER PRIMARY KEY AUTOINCREMENT, state_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, entity_id TEXT NOT NULL, state_type TEXT NOT NULL, value_json TEXT NOT NULL, value_commitment TEXT NOT NULL, source_ref TEXT NOT NULL, source_type TEXT NOT NULL, observed_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_until TEXT NOT NULL, confidence REAL NOT NULL, provenance_json TEXT NOT NULL, causal_parent_ids_json TEXT NOT NULL, ontology_version TEXT NOT NULL, policy_version TEXT NOT NULL, schema_version INTEGER NOT NULL, created_by TEXT NOT NULL, verified INTEGER NOT NULL, authority_rank INTEGER NOT NULL, freshness_half_life_seconds INTEGER NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL, supersedes_state_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, state_id), UNIQUE (tenant_id, entity_id, state_type, revision))")
        self.conn.execute("CREATE TABLE aporia_product_state_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, source_event_ref TEXT NOT NULL, state_revision INTEGER NOT NULL, envelope_json TEXT NOT NULL, candidate_count INTEGER NOT NULL, selected_count INTEGER NOT NULL, excluded_count INTEGER NOT NULL, rejected_count INTEGER NOT NULL, policy_version TEXT NOT NULL, schema_version INTEGER NOT NULL, valid_until TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, snapshot_id), UNIQUE (tenant_id, source_event_ref))")
        self.conn.execute("CREATE TABLE aporia_product_context_exposures (id INTEGER PRIMARY KEY AUTOINCREMENT, exposure_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, snapshot_id TEXT NOT NULL, state_revision INTEGER NOT NULL, mode TEXT NOT NULL, envelope_commitment TEXT NOT NULL, receipt_signature TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, exposure_id), UNIQUE (tenant_id, turn_ref))")
        self.conn.execute("CREATE TABLE aporia_product_runtime_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, outcome_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, exposure_id TEXT NOT NULL, decision_commitment TEXT NOT NULL, helpful INTEGER, latency_ms INTEGER NOT NULL, cost_units REAL NOT NULL, critical_regression INTEGER NOT NULL, evaluability TEXT NOT NULL, reason_codes_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, outcome_id), UNIQUE (tenant_id, exposure_id))")
        self.conn.execute("CREATE TABLE aporia_product_lacuna_one_shots (id INTEGER PRIMARY KEY AUTOINCREMENT, one_shot_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, session_binding TEXT NOT NULL, turn_binding TEXT NOT NULL, instrument TEXT NOT NULL, capability_hash TEXT NOT NULL, nonce_base64 TEXT NOT NULL, ciphertext_base64 TEXT NOT NULL, input_commitment TEXT NOT NULL, output_commitment TEXT, destruction_receipt TEXT, schema_version INTEGER NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, one_shot_id))")
        self.conn.execute("CREATE TABLE aporia_effect_contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, operation_id TEXT NOT NULL, mode TEXT NOT NULL, decision TEXT NOT NULL, allowed INTEGER NOT NULL, reversible INTEGER NOT NULL, external_effect INTEGER NOT NULL, idempotent INTEGER NOT NULL, approval_required INTEGER NOT NULL, guarded_eligible INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE aporia_effect_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, operation_id TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, operation_id))")
        self.conn.execute("CREATE TABLE ai_provider_response_metadata (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, incomplete_reason TEXT, error_code TEXT, input_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, latency_ms INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute("CREATE TABLE aporia_independent_control_switches (tenant_id INTEGER NOT NULL, switch_name TEXT NOT NULL, engaged INTEGER NOT NULL, revision INTEGER NOT NULL, changed_by_user_id INTEGER NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, switch_name))")
        self.conn.execute("CREATE TABLE aporia_independent_control_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, switch_name TEXT NOT NULL, state_before INTEGER NOT NULL, state_after INTEGER NOT NULL, revision INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, actor_user_id INTEGER NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, event_id), UNIQUE (tenant_id, operation_id))")

    def test_authority_freshness_and_selection_are_tenant_scoped(self) -> None:
        repository = AporiaProductStateRepository(self.conn)
        now = datetime.now(timezone.utc)
        confirmed = repository.record(self._item(now, "backend_fact", "commitment", {"state": "confirmed"}, True, 0.95))
        rejected = repository.record(self._item(now, "aporia_inference", "commitment", {"state": "guessed"}, False, 0.90))
        snapshot = repository.materialize_snapshot(49, "event-product-1", now.strftime("%Y-%m-%d %H:%M:%S.%f"))

        self.assertTrue(confirmed["accepted"])
        self.assertFalse(rejected["accepted"])
        self.assertEqual("lower_authority_conflict", rejected["reason_code"])
        self.assertTrue(len(snapshot["envelope"]["relevant_commitments"]) > 0)
        rejected_audits = [r for r in snapshot["envelope"]["selection_audit"] if r["selection"] == "rejected"]
        self.assertEqual(1, len(rejected_audits))
        self.assertIsNotNone(repository.latest_snapshot(49, now))
        self.assertIsNone(repository.latest_snapshot(50, now))

    def test_inference_cannot_be_persisted_as_confirmed_fact(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            AporiaProductStateRepository(self.conn).record(
                self._item(datetime.now(timezone.utc), "model_hypothesis", "confirmed_fact", {"unsafe": True}, False, 0.9)
            )
        self.assertEqual("aporia_product_state_authority_invalid", str(ctx.exception))

    def test_unknown_perspective_observation_cannot_become_fact_or_commitment(self) -> None:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        repository = AporiaProductStateRepository(self.conn)

        recorded = repository.record_perspective_observation({
            "tenant_id": 49,
            "event_id": "event-unknown-relationship",
            "ingested_at": now_str,
        }, {
            "observation_id": "observation-unknown-relationship",
            "perspective": "relationship_commitment",
            "subject_ref": "relationship:17",
            "epistemic_status": "unknown",
            "value": {"state": "unknown"},
            "evidence_event_ids": ["event-unknown-relationship"],
        })
        snapshot = repository.materialize_snapshot(
            49,
            "event-unknown-relationship",
            now_str,
        )
        row = self.conn.query("SELECT state_type, source_type, verified FROM aporia_product_state_items LIMIT 1").fetch_one()

        self.assertTrue(recorded["accepted"])
        self.assertIsNotNone(row)
        self.assertEqual("uncertainty", row["state_type"])
        self.assertEqual("aporia_inference", row["source_type"])
        self.assertEqual(0, int(row["verified"]))
        self.assertEqual([], snapshot["envelope"]["confirmed_facts"])
        self.assertEqual([], snapshot["envelope"]["relevant_commitments"])
        self.assertEqual(1, len(snapshot["envelope"]["uncertainty"]))

    def test_prepared_product_context_exposure_outcome_and_feedback_are_independent_from_experiments(self) -> None:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        repository = AporiaProductStateRepository(self.conn)
        repository.record(self._item(now, "backend_fact", "confirmed_fact", {"status": "active"}, True, 0.99))
        repository.materialize_snapshot(49, "event-product-2", now_str)
        service = AporiaProductContextService(self.conn, "secret", "test", self._controls())

        prepared = service.augment({"normal": True}, 49, "turn-product-1", "advisory", False)
        self.assertIn("aporia_product", prepared)
        self.assertIn("aporia_delivery_receipt", prepared)
        self.assertNotIn("selection_audit", prepared["aporia_product"]["context_envelope"])
        self.assertIn("selection_audit", repository.latest_snapshot(49, now)["envelope"])
        service.record_prepared_exposure(49, "turn-product-1", prepared["aporia_delivery_receipt"])
        outcome = service.record_outcome(49, "turn-product-1", "a" * 64, 120, 1.5, False)
        feedback = service.record_feedback(49, "turn-product-1", True)

        self.assertTrue(outcome["recorded"])
        self.assertEqual("observed", feedback["evaluability"])
        self.assertEqual(1, int(self.conn.query("SELECT helpful FROM aporia_product_runtime_outcomes").fetch_column()))
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'aporia_experiment_%'").fetch_column()))

    def test_zero_revision_snapshot_can_be_exposed_before_any_canonical_state_exists(self) -> None:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        repository = AporiaProductStateRepository(self.conn)
        snapshot = repository.materialize_snapshot(
            49,
            "event-product-zero-revision",
            now_str,
        )
        service = AporiaProductContextService(self.conn, "secret", "test", self._controls())

        prepared = service.augment(
            {"normal": True},
            49,
            "turn-product-zero-revision",
            "guarded_reversible",
            False,
        )
        service.record_prepared_exposure(
            49,
            "turn-product-zero-revision",
            prepared["aporia_delivery_receipt"],
        )

        self.assertEqual(0, snapshot["state_revision"])
        self.assertEqual(1, int(self.conn.query(
            "SELECT COUNT(*) FROM aporia_product_context_exposures WHERE state_revision = 0"
        ).fetch_column()))

    def test_memory_write_control_blocks_product_persistence_but_keeps_read_only_context(self) -> None:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        repository = AporiaProductStateRepository(self.conn)
        repository.record(self._item(now, "backend_fact", "confirmed_fact", {"status": "active"}, True, 0.99))
        repository.materialize_snapshot(49, "event-memory-kill", now_str)
        controls = self._controls()
        controls.set(49, AporiaIndependentControlPlane.MEMORY_WRITES, True, 7, "memory_write_test")
        service = AporiaProductContextService(self.conn, "secret", "test", controls)

        prepared = service.augment({"normal": True}, 49, "turn-memory-kill", "guarded_reversible", True)
        self.assertIn("aporia_product", prepared)
        self.assertIn("aporia_delivery_receipt", prepared)
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_product_context_exposures").fetch_column()))

        with self.assertRaises(RuntimeError) as ctx:
            service.record_prepared_exposure(49, "turn-memory-kill", prepared["aporia_delivery_receipt"])
        self.assertEqual("aporia_product_memory_writes_disabled", str(ctx.exception))

        outcome = service.record_outcome(49, "turn-memory-kill", "b" * 64, 100, 1.0, False)
        feedback = service.record_feedback(49, "turn-memory-kill", True)
        self.assertFalse(outcome["recorded"])
        self.assertFalse(feedback["recorded"])
        self.assertEqual(["aporia_memory_writes_kill_switch"], outcome["reason_codes"])
        self.assertEqual(["aporia_memory_writes_kill_switch"], feedback["reason_codes"])
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_product_runtime_outcomes").fetch_column()))

    def test_product_lacuna_is_bound_one_shot_ephemeral_and_replay_safe(self) -> None:
        gateway = AporiaProductLacunaGateway(self.conn, "product-lacuna-secret")
        state = {
            "hypotheses": ["A", "B"],
            "alternatives": ["plan-a", "plan-b"],
            "constraints": ["tenant_scope"],
        }
        prepared = gateway.prepare(49, "session-1", "turn-1", "discard_alternative_v1", state, 60)
        consumed = gateway.consume(49, "session-1", "turn-1", prepared["one_shot_id"], prepared["capability_token"])

        self.assertTrue(consumed["consumed"])
        self.assertEqual(["plan-a"], consumed["result"]["alternatives"])
        self.assertRegex(consumed["destruction_receipt"], r"^[0-9a-f]{64}$")
        self.assertEqual("", self.conn.query("SELECT ciphertext_base64 FROM aporia_product_lacuna_one_shots").fetch_column())
        self.assertEqual("consumed", self.conn.query("SELECT status FROM aporia_product_lacuna_one_shots").fetch_column())

        with self.assertRaises(RuntimeError) as ctx:
            gateway.consume(49, "session-1", "turn-1", prepared["one_shot_id"], prepared["capability_token"])
        self.assertEqual("aporia_product_lacuna_capability_consumed", str(ctx.exception))

    def test_product_lacuna_failure_falls_back_without_changing_input(self) -> None:
        gateway = AporiaProductLacunaGateway(self.conn, "product-lacuna-secret")
        state = {"confirmed_facts": ["must-not-be-accepted"]}
        result = gateway.evaluate_safely(49, "session-2", "turn-2", "focus_constraints_v1", state)

        self.assertFalse(result["available"])
        self.assertEqual({"confirmed_facts": ["must-not-be-accepted"]}, state)
        self.assertEqual(0, int(self.conn.query("SELECT COUNT(*) FROM aporia_product_lacuna_one_shots").fetch_column()))

    def test_expired_product_lacuna_capability_is_destroyed_and_persistently_rejected(self) -> None:
        gateway = AporiaProductLacunaGateway(self.conn, "product-lacuna-secret")
        prepared = gateway.prepare(49, "session-expired", "turn-expired", "focus_constraints_v1", {
            "hypotheses": ["unverified"],
            "constraints": ["tenant_scope"],
        }, 60)
        self.conn.execute("UPDATE aporia_product_lacuna_one_shots SET expires_at = '2000-01-01 00:00:00.000000'")

        with self.assertRaises(RuntimeError) as ctx:
            gateway.consume(
                49,
                "session-expired",
                "turn-expired",
                prepared["one_shot_id"],
                prepared["capability_token"],
            )
        self.assertEqual("aporia_product_lacuna_capability_expired", str(ctx.exception))

        row = self.conn.query(
            "SELECT status, ciphertext_base64, nonce_base64, destruction_receipt FROM aporia_product_lacuna_one_shots"
        ).fetch_one()
        self.assertIsNotNone(row)
        self.assertEqual("expired", row["status"])
        self.assertEqual("", row["ciphertext_base64"])
        self.assertEqual("", row["nonce_base64"])
        self.assertRegex(str(row["destruction_receipt"]), r"^[0-9a-f]{64}$")

    def test_lacuna_state_can_influence_constraints_but_cannot_become_canonical_fact(self) -> None:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        repository = AporiaProductStateRepository(self.conn)
        repository.record(self._item(
            now,
            "aporia_inference",
            "lacuna_state",
            {"constraints": ["effect_request", "policy_mutation"], "hypotheses": []},
            False,
            0.60,
        ))
        snapshot = repository.materialize_snapshot(49, "event-product-lacuna", now_str)

        self.assertEqual(1, len(snapshot["envelope"]["active_constraints"]))
        self.assertEqual("lacuna_state", snapshot["envelope"]["active_constraints"][0]["state_type"])
        self.assertFalse(snapshot["envelope"]["active_constraints"][0]["verified"])
        self.assertEqual([], snapshot["envelope"]["confirmed_facts"])

    def test_operational_metrics_expose_quality_cost_integrity_and_alerts_without_content(self) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        self.conn.execute(f"""INSERT INTO aporia_product_state_snapshots
            (snapshot_id, tenant_id, source_event_ref, state_revision, envelope_json, candidate_count,
             selected_count, excluded_count, rejected_count, policy_version, schema_version, valid_until, created_at)
            VALUES ('snapshot-metrics', 49, 'event-metrics', 1, '{{}}', 4, 2, 1, 1, 'policy', 1, '2099-01-01 00:00:00', '{now}')""")
        self.conn.execute(f"""INSERT INTO aporia_product_context_exposures
            (exposure_id, tenant_id, turn_ref, snapshot_id, state_revision, mode, envelope_commitment, receipt_signature, created_at)
            VALUES ('exposure-metrics', 49, 'turn-metrics', 'snapshot-metrics', 1, 'guarded_reversible', 'commitment', 'signature', '{now}')""")
        self.conn.execute(f"""INSERT INTO aporia_product_runtime_outcomes
            (outcome_id, tenant_id, exposure_id, decision_commitment, helpful, latency_ms, cost_units,
             critical_regression, evaluability, reason_codes_json, created_at, updated_at)
            VALUES ('outcome-metrics', 49, 'exposure-metrics', 'decision', 1, 120, 1.25, 0, 'observed', '[]', '{now}', '{now}')""")
        self.conn.execute(f"""INSERT INTO aporia_effect_contracts
            (tenant_id, operation_id, mode, decision, allowed, reversible, external_effect,
             idempotent, approval_required, guarded_eligible, created_at)
            VALUES (49, 'operation-metrics', 'guarded_reversible', 'safe_prefix_allowed', 1, 1, 0, 1, 1, 1, '{now}')""")
        self.conn.execute(f"""INSERT INTO aporia_effect_outcomes (tenant_id, operation_id, state, created_at)
            VALUES (49, 'operation-metrics', 'executed', '{now}')""")
        self.conn.execute(f"""INSERT INTO ai_provider_response_metadata
            (status, incomplete_reason, error_code, input_tokens, cached_tokens, output_tokens,
             reasoning_tokens, total_tokens, latency_ms, created_at)
            VALUES ('completed', NULL, NULL, 100, 50, 20, 10, 120, 80, '{now}')""")

        metrics = AporiaProductOperationalMetrics(self.conn)
        healthy = metrics.report(49, 24)

        self.assertEqual("healthy", healthy["status"])
        self.assertEqual(1.0, healthy["quality"]["helpful_rate"])
        self.assertEqual(120, healthy["latency_cost"]["outcome_latency_p95_ms"])
        self.assertIsNone(healthy["latency_cost"]["provider_cached_ratio"])
        self.assertEqual("unavailable_per_tenant", healthy["latency_cost"]["provider_scope"])
        self.assertEqual(0, sum(healthy["integrity"].values()))
        self.assertNotIn("content", healthy)

        global_metrics = metrics.report(None, 24)
        self.assertEqual(0.5, global_metrics["latency_cost"]["provider_cached_ratio"])
        self.assertEqual("global", global_metrics["latency_cost"]["provider_scope"])

        self.conn.execute("UPDATE aporia_product_context_exposures SET snapshot_id = 'snapshot-other-tenant'")
        critical = metrics.report(49, 24)
        self.assertEqual("critical", critical["status"])
        self.assertEqual("aporia_cross_tenant_integrity_failure", critical["alerts"][0]["code"])

    def _controls(self) -> AporiaIndependentControlPlane:
        return AporiaIndependentControlPlane(
            self.conn,
            Environment("/tmp", {"APP_ENV": "staging"}),
        )

    def _item(
        self,
        now: datetime,
        source_type: str,
        state_type: str,
        value: dict,
        verified: bool,
        confidence: float,
    ) -> dict:
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        future_str = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
        val_json = json.dumps(value, separators=(",", ":"), sort_keys=True)
        return {
            "tenant_id": 49,
            "entity_id": "relationship:17",
            "state_type": state_type,
            "value": value,
            "source_ref": "source-" + hashlib.sha256(val_json.encode("utf-8")).hexdigest(),
            "source_type": source_type,
            "observed_at": now_str,
            "valid_from": now_str,
            "valid_until": future_str,
            "confidence": confidence,
            "provenance": {"source": source_type},
            "causal_parent_ids": ["event-1"],
            "ontology_version": hashlib.sha256("ontology-v1".encode("utf-8")).hexdigest(),
            "policy_version": hashlib.sha256("policy-v1".encode("utf-8")).hexdigest(),
            "schema_version": 1,
            "created_by": "test",
            "verified": verified,
            "freshness_half_life_seconds": 3600,
        }


class TestAporiaAdvancedPhases(unittest.TestCase):
    def test_consistent_sections_produce_near_zero_residual(self) -> None:
        observations = [
            {"scope": "world", "vector": [1.0, 0.5], "weight": 1.0},
            {"scope": "world", "vector": [1.0, 0.5], "weight": 1.0},
            {"scope": "self", "vector": [0.5, 1.0], "weight": 1.0},
            {"scope": "self", "vector": [0.5, 1.0], "weight": 1.0},
        ]
        result = AporiaObstructionEngine().evaluate(
            observations,
            [{"left": 0, "right": 1}, {"left": 1, "right": 2}, {"left": 2, "right": 3}, {"left": 3, "right": 0}],
            [],
            True,
            AporiaScientificExperiment.SEEDS,
        )

        self.assertEqual("globally_consistent", result["classification"])
        self.assertLess(result["world_score"], 0.00001)
        self.assertLess(result["self_score"], 0.00001)
        self.assertLess(result["cycle_score"], 0.00001)

    def test_factual_contradiction_is_classified_as_world_conflict(self) -> None:
        result = AporiaObstructionEngine().evaluate(
            [
                {"scope": "world", "vector": [1.0], "weight": 1.0},
                {"scope": "world", "vector": [1.0], "weight": 1.0},
            ],
            [],
            [
                {"variable": "invoice_paid", "value": True, "commitment": "a" * 64},
                {"variable": "invoice_paid", "value": False, "commitment": "b" * 64},
            ],
            False,
            AporiaScientificExperiment.SEEDS,
        )

        self.assertEqual("world_conflict", result["classification"])
        self.assertFalse(result["satisfiable"])
        self.assertEqual(["a" * 64, "b" * 64], result["unsat_constraint_hashes"])

    def test_transport_correction_removes_correctable_self_residual(self) -> None:
        result = AporiaObstructionEngine().evaluate(
            [
                {"scope": "world", "vector": [1.0, 1.0], "weight": 1.0},
                {"scope": "world", "vector": [1.0, 1.0], "weight": 1.0},
                {"scope": "self", "vector": [1.0, 0.0], "weight": 1.0, "transport_correctable": True},
                {"scope": "self", "vector": [0.0, 1.0], "weight": 1.0, "transport_correctable": True},
            ],
            [],
            [],
            True,
            AporiaScientificExperiment.SEEDS,
        )

        self.assertEqual("transport_error", result["classification"])
        self.assertGreaterEqual(result["self_score"], 0.25)
        self.assertLess(result["corrected_self_score"], 0.10)

    def test_synthetic_non_global_cycle_is_detected_as_self_obstruction_candidate(self) -> None:
        result = AporiaObstructionEngine().evaluate(
            [
                {"scope": "world", "vector": [1.0, 1.0], "weight": 1.0},
                {"scope": "world", "vector": [1.0, 1.0], "weight": 1.0},
                {"scope": "self", "vector": [1.0, 0.0], "weight": 1.0},
                {"scope": "self", "vector": [0.0, 1.0], "weight": 1.0},
            ],
            [
                {"left": 0, "right": 1, "delta": [1.0, 0.0]},
                {"left": 1, "right": 2, "delta": [1.0, 0.0]},
                {"left": 2, "right": 0, "delta": [1.0, 0.0]},
            ],
            [],
            True,
            AporiaScientificExperiment.SEEDS,
        )

        self.assertEqual("self_obstruction_candidate", result["classification"])
        self.assertGreaterEqual(result["cycle_score"], 0.25)

    def test_semantic_equivalent_effects_compile_to_the_same_canonical_effect(self) -> None:
        compiler = AporiaEffectCompiler()
        authority = "c" * 64
        first = compiler.compile(49, "profile_metrics", {"days": 30, "profile_id": 7}, authority)
        second = compiler.compile(49, "profile_metrics", {"profile_id": 7, "days": 30}, authority)

        self.assertEqual(first["effect_commitment"], second["effect_commitment"])
        self.assertEqual(first["atoms"], second["atoms"])

    def test_meet_does_not_average_incompatible_parameters_or_promote_danger(self) -> None:
        compiler = AporiaEffectCompiler()
        authority = "d" * 64
        left = compiler.compile(49, "profile_metrics", {"days": 30}, authority)
        right = compiler.compile(49, "profile_metrics", {"days": 90}, authority)
        right["external"] = True
        right["risk"] = 0.95
        meet = compiler.meet([left, right])

        self.assertEqual(["days"], meet["incompatible_parameters"])
        self.assertIn("incompatible_parameters_not_reconciled", meet["reason_codes"])
        self.assertIn("dangerous_effect_not_invariant", meet["reason_codes"])
        self.assertNotIn("external:true", meet["safe_prefix_atoms"])

    def test_empty_meet_produces_evidence_deadlock(self) -> None:
        compiler = AporiaEffectCompiler()
        meet = compiler.meet([
            {"tenant_id": 49, "atoms": ["left:only"], "external": False, "risk": 0.1},
            {"tenant_id": 49, "atoms": ["right:only"], "external": False, "risk": 0.1},
        ])

        self.assertTrue(meet["deadlock"])
        self.assertIn("no_safe_invariant_prefix", meet["reason_codes"])

    def test_preregistered_assignment_and_bootstrap_are_deterministic_and_retain_negative_results(self) -> None:
        experiment = AporiaScientificExperiment()
        episode = "e" * 64
        first = experiment.assignment(49, episode, "secret")
        second = experiment.assignment(49, episode, "secret")
        pairs = [{"control": 10.0, "treatment": 8.0} for _ in range(12)]
        evaluation = experiment.evaluate(pairs, "factual_consistency", 500)

        self.assertEqual(first, second)
        self.assertIn(first["arm"], AporiaScientificExperiment.ARMS)
        self.assertEqual("observed", evaluation["evaluability"])
        self.assertEqual(-2.0, evaluation["absolute_effect"])
        self.assertTrue(evaluation["negative_result"])
        self.assertIn("negative_results_retained", evaluation["reason_codes"])
        self.assertTrue(experiment.preregistration()["posthoc_metric_selection_forbidden"])

    def test_insufficient_paired_tasks_remain_explicitly_not_evaluable(self) -> None:
        result = AporiaScientificExperiment().evaluate(
            [{"control": 1.0, "treatment": 2.0} for _ in range(7)],
            "path_dependence",
        )

        self.assertEqual("not_evaluable", result["evaluability"])
        self.assertEqual(["insufficient_paired_tasks"], result["reason_codes"])

    def test_bootstrap_for_eight_pairs_samples_with_replacement(self) -> None:
        pairs = (
            [{"control": 0.0, "treatment": 1.0} for _ in range(2)]
            + [{"control": 1.0, "treatment": 1.0} for _ in range(6)]
        )
        result = AporiaScientificExperiment().evaluate(pairs, "path_dependence", 5000)

        self.assertEqual(0.25, result["absolute_effect"])
        self.assertEqual(0.0, result["confidence_interval_95"][0])
        self.assertGreater(result["confidence_interval_95"][1], 0.25)
        self.assertIn("bootstrap_sampler_v2", result["reason_codes"])
        self.assertEqual(2, result["version"])

    def test_guarded_mode_requires_approval_canary_reversibility_limits_and_supports_rollback(self) -> None:
        conn = self._guarded_database()
        gateway = AporiaHirtGateway(conn, "secret", "test")
        authority = "f" * 64
        inactive_episode = "1" * 64
        stmt = conn.prepare("INSERT INTO aporia_experiment_assignments (tenant_id, episode_ref, arm, experiment_key) VALUES (?, ?, 'C5', 'aporia_guarded_reversible_v1')")
        stmt.execute([49, inactive_episode])
        inactive = gateway.inspect(49, inactive_episode, "profile_metrics", {"days": 30}, authority, "guarded_reversible")

        self.assertFalse(inactive["allowed"])
        self.assertIn("guarded_policy_inactive", inactive["reason_codes"])

        commitment = "a" * 64
        policy = AporiaGuardedPolicy(conn, "test")
        policy.approve(49, 7, commitment, 0.25, 1, 2.0)
        policy.activate(49, 7, commitment)
        active_episode = "2" * 64
        stmt.execute([49, active_episode])
        allowed = gateway.inspect(49, active_episode, "profile_metrics", {"days": 30}, authority, "guarded_reversible")

        self.assertTrue(allowed["allowed"])
        self.assertEqual("safe_prefix_allowed", allowed["decision"])
        self.assertEqual(1, int(conn.query("SELECT call_count FROM aporia_guarded_usage").fetch_column()))

        limited_episode = "3" * 64
        stmt.execute([49, limited_episode])
        limited = gateway.inspect(49, limited_episode, "profile_metrics", {"days": 30}, authority, "guarded_reversible")
        self.assertFalse(limited["allowed"])
        self.assertIn("guarded_daily_limit_exceeded", limited["reason_codes"])

        policy.rollback(49, 7, commitment)
        self.assertEqual("rolled_back", conn.query("SELECT status FROM aporia_guarded_policies").fetch_column())
        self.assertEqual(1, int(conn.query("SELECT kill_switch FROM aporia_guarded_policies").fetch_column()))
        self.assertEqual(3, int(conn.query("SELECT COUNT(*) FROM aporia_guarded_policy_events").fetch_column()))

    def test_product_guarded_mode_allows_only_compensable_email_preparation_without_scientific_assignment(self) -> None:
        conn = self._guarded_database()
        policy = AporiaGuardedPolicy(conn, "test")
        commitment = "a" * 64
        policy.approve(49, 7, commitment, 0.05, 10, 10.0)
        policy.activate(49, 7, commitment)
        gateway = AporiaProductHirtGateway(conn, "secret", "test")
        authority = "f" * 64
        episode = "4" * 64
        parameters = {
            "to_email": "maria@example.test",
            "subject": "Olá",
            "body_html": "<p>Olá</p>",
            "idempotency_key": "prepare-email-contract-0001",
        }

        allowed = gateway.inspect(49, episode, "prepare_email", parameters, authority, "guarded_reversible")
        deduplicated = gateway.inspect(49, episode, "prepare_email", parameters, authority, "guarded_reversible")
        blocked = gateway.inspect(49, "5" * 64, "send_email", {
            "draft_id": 1,
            "idempotency_key": "send-email-contract-0001",
        }, authority, "guarded_reversible")

        self.assertTrue(allowed["allowed"])
        self.assertEqual("safe_prefix_allowed", allowed["decision"])
        self.assertEqual("discard_email_draft", allowed["compensation"])
        self.assertTrue(allowed["reversible"])
        self.assertTrue(allowed["idempotent"])
        self.assertTrue(allowed["approval_required"])
        self.assertTrue(allowed["guarded_eligible"])
        self.assertEqual("agent_tool_permission_gate", allowed["approval_basis"])
        self.assertRegex(allowed["policy_version"], r"^[0-9a-f]{64}$")
        self.assertTrue(deduplicated["deduplicated"])
        self.assertEqual(1, int(conn.query("SELECT call_count FROM aporia_guarded_usage").fetch_column()))
        self.assertFalse(blocked["allowed"])
        self.assertIn("effect_not_on_guarded_allowlist", blocked["reason_codes"])
        self.assertEqual(0, int(conn.query("SELECT COUNT(*) FROM aporia_experiment_assignments WHERE experiment_key = 'aporia_guarded_reversible_v1'").fetch_column()))

    def test_product_hirt_verifies_and_compensates_email_draft_postconditions(self) -> None:
        conn = self._guarded_database()
        policy = AporiaGuardedPolicy(conn, "test")
        commitment = "b" * 64
        policy.approve(49, 7, commitment, 0.05, 10, 10.0)
        policy.activate(49, 7, commitment)
        gateway = AporiaProductHirtGateway(conn, "secret", "test")
        inspection = gateway.inspect(49, "6" * 64, "prepare_email", {
            "to_email": "maria@example.test",
            "subject": "Olá",
            "body_html": "<p>Olá</p>",
            "idempotency_key": "prepare-email-contract-0002",
        }, "e" * 64, "guarded_reversible")
        conn.execute("INSERT INTO assistant_email_drafts (id, user_id, status) VALUES (17, 49, 'pending')")

        outcome = gateway.complete(49, inspection["operation_id"], {"draft_id": 17, "status": "invalid"})

        self.assertFalse(outcome["verified"])
        self.assertTrue(outcome["compensated"])
        self.assertEqual("discarded", conn.query("SELECT status FROM assistant_email_drafts WHERE id = 17").fetch_column())
        self.assertEqual("compensated", conn.query("SELECT state FROM aporia_effect_outcomes").fetch_column())
        self.assertEqual("email_draft_compensated", conn.query("SELECT reason_code FROM aporia_effect_outcomes").fetch_column())

    def _guarded_database(self) -> Connection:
        conn = Connection()
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE aporia_experiment_assignments (tenant_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, arm TEXT NOT NULL, experiment_key TEXT NOT NULL DEFAULT 'aporia_c0_c5_v1', UNIQUE (tenant_id, episode_ref, experiment_key))")
        conn.execute("CREATE TABLE aporia_guarded_policies (tenant_id INTEGER PRIMARY KEY, status TEXT NOT NULL, approval_commitment TEXT, approved_by_user_id INTEGER, risk_threshold REAL NOT NULL, daily_call_limit INTEGER NOT NULL, daily_cost_limit REAL NOT NULL, kill_switch INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE aporia_guarded_policy_events (id INTEGER PRIMARY KEY AUTOINCREMENT, policy_event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, status TEXT NOT NULL, approval_commitment TEXT, approved_by_user_id INTEGER, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, UNIQUE (tenant_id, policy_event_id), UNIQUE (tenant_id, operation_id))")
        conn.execute("CREATE TABLE aporia_guarded_usage (tenant_id INTEGER NOT NULL, usage_date TEXT NOT NULL, call_count INTEGER NOT NULL, cost_units REAL NOT NULL, PRIMARY KEY (tenant_id, usage_date))")
        conn.execute("CREATE TABLE aporia_hirt_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, action_commitment TEXT NOT NULL, mode TEXT NOT NULL, effect_commitment TEXT NOT NULL, meet_commitment TEXT NOT NULL, decision TEXT NOT NULL, allowed INTEGER NOT NULL, reversible INTEGER NOT NULL, external INTEGER NOT NULL, risk REAL NOT NULL, safe_prefix_count INTEGER NOT NULL, reason_codes_json TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, UNIQUE (tenant_id, decision_id), UNIQUE (tenant_id, operation_id))")
        conn.execute("CREATE TABLE aporia_effect_contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, operation_id TEXT NOT NULL, action_name TEXT NOT NULL, action_commitment TEXT NOT NULL, mode TEXT NOT NULL, domain_name TEXT NOT NULL, operation_name TEXT NOT NULL, resource_commitment TEXT NOT NULL, effect_commitment TEXT NOT NULL, meet_commitment TEXT NOT NULL, decision TEXT NOT NULL, allowed INTEGER NOT NULL, reversible INTEGER NOT NULL, external_effect INTEGER NOT NULL, idempotent INTEGER NOT NULL, approval_required INTEGER NOT NULL, guarded_eligible INTEGER NOT NULL, compensation TEXT NULL, risk REAL NOT NULL, cost_units REAL NOT NULL, preconditions_json TEXT NOT NULL, postconditions_json TEXT NOT NULL, reason_codes_json TEXT NOT NULL, authority_commitment TEXT NOT NULL, idempotency_key_commitment TEXT NULL, policy_version TEXT NOT NULL, approval_basis TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, causal_parent_ids_json TEXT NOT NULL, maximum_scope_json TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, actor_type TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, contract_id), UNIQUE (tenant_id, operation_id))")
        conn.execute("CREATE TABLE aporia_effect_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, outcome_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, operation_id TEXT NOT NULL, state TEXT NOT NULL, result_commitment TEXT NOT NULL, reason_code TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, outcome_id), UNIQUE (tenant_id, operation_id))")
        conn.execute("CREATE TABLE assistant_email_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, status TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        return conn


if __name__ == "__main__":
    unittest.main()
