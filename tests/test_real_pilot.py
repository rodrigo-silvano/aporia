import hashlib
import json
import os
import re
import unittest

from aporia.application.advisory_context import AporiaAdvisoryContextService
from aporia.application.runtime_mode import AporiaRuntimeMode
from aporia.application.runtime_mode_resolver import AporiaRuntimeModeResolver
from aporia.config import Environment
from aporia.infrastructure.db import Connection
from aporia.infrastructure.guarded_policy import AporiaGuardedPolicy
from aporia.infrastructure.shadow_twins import AporiaShadowLifecycleLedger


class TestAporiaRealPilot(unittest.TestCase):
    def test_runtime_mode_is_tenant_scoped_and_global_mode_still_takes_precedence(self):
        prev_env = os.environ.get('APP_ENV')
        os.environ['APP_ENV'] = 'staging'
        try:
            pilot = Environment('/tmp', {
                'APORIA_MODE': 'disabled',
                'APP_ENV': 'staging',
                'APORIA_REAL_PILOT_MODE': 'canary',
                'APORIA_REAL_PILOT_TENANT_IDS': '73,49,49',
                'APORIA_REAL_PILOT_APPROVAL_COMMITMENT': 'a' * 64,
            })

            self.assertEqual('advisory', AporiaRuntimeMode.for_tenant(pilot, 49))
            self.assertEqual('advisory', AporiaRuntimeMode.for_tenant(pilot, 73))
            self.assertEqual('disabled', AporiaRuntimeMode.for_tenant(pilot, 50))
            self.assertEqual([49, 73], AporiaRuntimeMode.pilot_tenant_ids(pilot))
            self.assertTrue(AporiaRuntimeMode.has_active_pilot(pilot))

            global_env = Environment('/tmp', {
                'APORIA_MODE': 'shadow',
                'APP_ENV': 'staging',
                'APORIA_REAL_PILOT_MODE': 'canary',
                'APORIA_REAL_PILOT_TENANT_IDS': '49',
                'APORIA_REAL_PILOT_APPROVAL_COMMITMENT': 'a' * 64,
            })
            self.assertEqual('shadow', AporiaRuntimeMode.for_tenant(global_env, 50))
        finally:
            if prev_env is None:
                os.environ.pop('APP_ENV', None)
            else:
                os.environ['APP_ENV'] = prev_env

    def test_real_pilot_preregisters_before_assignment_and_records_blinded_feedback(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-real-1', '10000000-0000-4000-a000-000000000001')
        self._prepare_lifecycle(conn, 'turn-real-1b', '10000000-0000-4000-a000-000000000001')
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')

        context = service.augment({}, 49, 'turn-real-1', 'advisory', 'aporia_real_pilot_v1', 'session-real-1')
        self.assertNotIn('aporia_experiment', context)
        self.assertNotIn('experiment_core_only', context)

        arm = conn.query('SELECT arm FROM aporia_experiment_assignments').fetchColumn(0)
        self.assertIn(arm, ['C0', 'C5'])
        self.assertEqual(1, int(conn.query('SELECT COUNT(*) FROM aporia_experiment_preregistrations').fetchColumn(0)))
        self.assertEqual(1, int(conn.query('SELECT assigned_before_outcome FROM aporia_experiment_assignments').fetchColumn(0)))

        metrics = json.loads(conn.query('SELECT metrics_json FROM aporia_experiment_preregistrations').fetchColumn(0))
        self.assertEqual([
            'explicit_helpfulness',
            'completion_without_critical_regression',
            'latency_ms',
            'cost_units',
        ], metrics)

        service.augment({}, 49, 'turn-real-1b', 'advisory', 'aporia_real_pilot_v1', 'session-real-1')
        rows = conn.query('SELECT arm FROM aporia_experiment_assignments ORDER BY created_at, episode_ref').fetchAll()
        arms = [r['arm'] for r in rows]
        self.assertEqual(2, len(arms))
        self.assertEqual(arms[0], arms[1])

        outcome = service.record_outcome(
            49,
            'turn-real-1',
            hashlib.sha256(b'decision').hexdigest(),
            125,
            17.0,
            False,
            'aporia_real_pilot_v1'
        )
        self.assertEqual('not_evaluable', outcome['evaluability'])

        feedback = service.record_feedback(49, 'turn-real-1', True)
        self.assertEqual('observed', feedback['evaluability'])
        self.assertEqual(1, int(conn.query('SELECT feedback_correct FROM aporia_advisory_outcomes').fetchColumn(0)))
        self.assertEqual('observed', conn.query('SELECT evaluability FROM aporia_advisory_outcomes').fetchColumn(0))
        self.assertEqual(1, int(conn.query('SELECT COUNT(*) FROM aporia_real_pilot_feedback_events').fetchColumn(0)))
        self.assertEqual('sync_user', conn.query('SELECT source FROM aporia_real_pilot_feedback_events').fetchColumn(0))

    def test_stopped_pilot_is_disabled_and_guarded_mode_requires_an_active_policy(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-stopped', '20000000-0000-4000-a000-000000000002')
        commitment = 'a' * 64
        canary = Environment('/tmp', {
            'APORIA_MODE': 'disabled',
            'APP_ENV': 'staging',
            'APORIA_REAL_PILOT_MODE': 'canary',
            'APORIA_REAL_PILOT_TENANT_IDS': '49',
            'APORIA_REAL_PILOT_APPROVAL_COMMITMENT': commitment,
        })
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')
        service.augment({}, 49, 'turn-stopped', 'advisory', 'aporia_real_pilot_v1')
        service.record_outcome(49, 'turn-stopped', hashlib.sha256(b'decision').hexdigest(), 20, 1.0, True, 'aporia_real_pilot_v1')

        self.assertEqual('disabled', AporiaRuntimeModeResolver.for_tenant(canary, conn, 49))

        saved_env = {k: os.environ.get(k) for k in ['APP_ENV', 'APORIA_REAL_PILOT_MODE', 'APORIA_REAL_PILOT_TENANT_IDS', 'APORIA_REAL_PILOT_APPROVAL_COMMITMENT']}
        os.environ['APP_ENV'] = 'staging'
        os.environ['APORIA_REAL_PILOT_MODE'] = 'guarded_reversible'
        os.environ['APORIA_REAL_PILOT_TENANT_IDS'] = '49'
        os.environ['APORIA_REAL_PILOT_APPROVAL_COMMITMENT'] = commitment
        try:
            guarded = Environment('/tmp', {
                'APORIA_MODE': 'disabled',
                'APP_ENV': 'staging',
                'APORIA_REAL_PILOT_MODE': 'guarded_reversible',
                'APORIA_REAL_PILOT_TENANT_IDS': '49',
                'APORIA_REAL_PILOT_APPROVAL_COMMITMENT': commitment,
            })
            self.assertEqual('guarded_reversible', AporiaRuntimeMode.for_tenant(guarded, 49))
            self.assertEqual('disabled', AporiaRuntimeModeResolver.for_tenant(guarded, conn, 49))
            policy = AporiaGuardedPolicy(conn, 'staging')
            policy.approve(49, 49, commitment, 0.05, 10, 10.0)
            policy.activate(49, 49, commitment)
            self.assertEqual('guarded_reversible', AporiaRuntimeModeResolver.for_tenant(guarded, conn, 49))
            self.assertEqual('disabled', AporiaRuntimeModeResolver.for_tenant(guarded, conn, 50))
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_guarded_assignment_is_always_c5_and_critical_regression_stops_policy_atomically(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-guarded', '30000000-0000-4000-a000-000000000003')
        commitment = 'b' * 64
        policy = AporiaGuardedPolicy(conn, 'staging')
        policy.approve(49, 49, commitment, 0.05, 10, 10.0)
        policy.activate(49, 49, commitment)
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')
        context = service.augment({}, 49, 'turn-guarded', 'guarded_reversible', 'aporia_guarded_reversible_v1')

        self.assertEqual('C5', context['aporia_experiment']['arm'])
        service.record_outcome(49, 'turn-guarded', hashlib.sha256(b'guarded-decision').hexdigest(), 30, 1.0, True, 'aporia_guarded_reversible_v1')

        self.assertEqual('rolled_back', conn.query('SELECT status FROM aporia_guarded_policies').fetchColumn(0))
        self.assertEqual(1, int(conn.query('SELECT kill_switch FROM aporia_guarded_policies').fetchColumn(0)))
        self.assertEqual('system', conn.query("SELECT actor_type FROM aporia_guarded_policy_events WHERE action_name = 'auto_stop_guarded_policy'").fetchColumn(0))
        self.assertEqual('critical_regression_observed', conn.query("SELECT reason_code FROM aporia_guarded_policy_events WHERE action_name = 'auto_stop_guarded_policy'").fetchColumn(0))

    def test_feedback_cannot_be_changed_after_it_was_recorded(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-real-2', '40000000-0000-4000-a000-000000000004')
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')
        service.augment({}, 49, 'turn-real-2', 'advisory', 'aporia_real_pilot_v1')
        service.record_outcome(49, 'turn-real-2', hashlib.sha256(b'decision').hexdigest(), 80, 9.0, False, 'aporia_real_pilot_v1')
        service.record_feedback(49, 'turn-real-2', False)

        with self.assertRaises(RuntimeError) as ctx:
            service.record_feedback(49, 'turn-real-2', True)
        self.assertIn('aporia_advisory_feedback_conflict', str(ctx.exception))

    def test_prepared_exposure_is_recorded_only_after_delivery_and_is_idempotent(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-real-prepared', '50000000-0000-4000-a000-000000000005')
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')

        context = service.augment(
            {},
            49,
            'turn-real-prepared',
            'advisory',
            'aporia_real_pilot_v1',
            'session-real-prepared',
            False
        )

        self.assertIn('aporia_delivery_receipt', context)
        self.assertRegex(context['aporia_delivery_receipt']['receipt_signature'], r'^[0-9a-f]{64}$')
        self.assertEqual(0, int(conn.query('SELECT COUNT(*) FROM aporia_advisory_exposures').fetchColumn(0)))

        service.record_prepared_exposure(
            49,
            'turn-real-prepared',
            context['aporia_delivery_receipt'],
            'aporia_real_pilot_v1'
        )
        service.record_prepared_exposure(
            49,
            'turn-real-prepared',
            context['aporia_delivery_receipt'],
            'aporia_real_pilot_v1'
        )

        self.assertEqual(1, int(conn.query('SELECT COUNT(*) FROM aporia_advisory_exposures').fetchColumn(0)))

    def test_prepared_exposure_rejects_an_altered_receipt(self):
        conn = Connection.in_memory()
        self._init_schema(conn)
        self._prepare_lifecycle(conn, 'turn-real-altered', '60000000-0000-4000-a000-000000000006')
        service = AporiaAdvisoryContextService(conn, 'bridge-secret', 'staging')
        context = service.augment(
            {},
            49,
            'turn-real-altered',
            'advisory',
            'aporia_real_pilot_v1',
            'session-real-altered',
            False
        )
        receipt = dict(context['aporia_delivery_receipt'])
        receipt['delivery'] = 'baseline' if receipt['delivery'] == 'advisory' else 'advisory'

        with self.assertRaises(RuntimeError) as ctx:
            service.record_prepared_exposure(49, 'turn-real-altered', receipt, 'aporia_real_pilot_v1')
        self.assertIn('aporia_advisory_exposure_invalid', str(ctx.exception))

    def _init_schema(self, conn: Connection):
        conn.executescript("""
        CREATE TABLE aporia_independent_control_switches (tenant_id INTEGER NOT NULL, switch_name TEXT NOT NULL, engaged INTEGER NOT NULL, revision INTEGER NOT NULL, changed_by_user_id INTEGER NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, switch_name));
        CREATE TABLE aporia_independent_control_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, switch_name TEXT NOT NULL, state_before INTEGER NOT NULL, state_after INTEGER NOT NULL, revision INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, actor_user_id INTEGER NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, event_id), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_experiment_preregistrations (experiment_key TEXT PRIMARY KEY, protocol_commitment TEXT NOT NULL, arms_json TEXT NOT NULL, hypotheses_json TEXT NOT NULL, metrics_json TEXT NOT NULL, seeds_json TEXT NOT NULL, locked_at TEXT NOT NULL);
        CREATE TABLE aporia_experiment_assignments (assignment_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, experiment_key TEXT NOT NULL, arm TEXT NOT NULL, seed INTEGER NOT NULL, assignment_commitment TEXT NOT NULL, assigned_before_outcome INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, assignment_id), UNIQUE (tenant_id, episode_ref, experiment_key));
        CREATE TABLE aporia_advisory_exposures (exposure_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, assignment_id TEXT NOT NULL, episode_ref TEXT NOT NULL, delivery TEXT NOT NULL, envelope_commitment TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, exposure_id), UNIQUE (tenant_id, episode_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_advisory_outcomes (advisory_outcome_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, assignment_id TEXT NOT NULL, episode_ref TEXT NOT NULL, decision_commitment TEXT NOT NULL, baseline_decision_commitment TEXT, decision_changed INTEGER, feedback_correct INTEGER, confidence REAL, brier_score REAL, calibration_error REAL, latency_ms INTEGER NOT NULL, cost_units REAL NOT NULL, critical_regression INTEGER NOT NULL, evaluability TEXT NOT NULL, reason_codes_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (tenant_id, advisory_outcome_id), UNIQUE (tenant_id, episode_ref));
        CREATE TABLE aporia_real_pilot_feedback_events (feedback_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, assignment_id TEXT NOT NULL, episode_ref TEXT NOT NULL, feedback_correct INTEGER NOT NULL, source TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, feedback_id), UNIQUE (tenant_id, episode_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_identity_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, continuity_score REAL NOT NULL, model_dependency TEXT NOT NULL, graph_count INTEGER NOT NULL, behavior_count INTEGER NOT NULL, commitment_count INTEGER NOT NULL, autobiography_count INTEGER NOT NULL, ontology_count INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_obstruction_measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, classification TEXT NOT NULL, world_score REAL NOT NULL, corrected_self_score REAL NOT NULL, cycle_score REAL NOT NULL, reason_codes_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_guarded_policies (tenant_id INTEGER PRIMARY KEY, status TEXT NOT NULL, approval_commitment TEXT, approved_by_user_id INTEGER, risk_threshold REAL NOT NULL, daily_call_limit INTEGER NOT NULL, daily_cost_limit REAL NOT NULL, kill_switch INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE aporia_guarded_policy_events (id INTEGER PRIMARY KEY AUTOINCREMENT, policy_event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, status TEXT NOT NULL, approval_commitment TEXT, approved_by_user_id INTEGER, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, UNIQUE (tenant_id, policy_event_id), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_shadow_episode_lifecycle (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, session_public_id TEXT NOT NULL, turn_id TEXT NOT NULL, turn_ref TEXT NOT NULL, operation_id TEXT NOT NULL, status TEXT NOT NULL, assigned_at TEXT, preparing_at TEXT, prepared_at TEXT, exposed_at TEXT, c0_prediction_sealed_at TEXT, c5_prediction_sealed_at TEXT, turn_done_at TEXT, usage_terminal_at TEXT, outcome_terminal_at TEXT, outcome_commitment TEXT, archived_at TEXT, revoked_at TEXT, timed_out_at TEXT, missing_json TEXT, runtime_influence INTEGER NOT NULL DEFAULT 0, external_effect INTEGER NOT NULL DEFAULT 0, contamination_detected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, turn_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_shadow_lifecycle_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, transition_name TEXT NOT NULL, arm TEXT, payload_commitment TEXT, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, state_before TEXT NOT NULL, state_after TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, event_id), UNIQUE (tenant_id, turn_ref, transition_name, arm));
        """)

    def _prepare_lifecycle(self, conn: Connection, turn_id: str, session_public_id: str):
        AporiaShadowLifecycleLedger(conn, 'bridge-secret', 'staging').prepare(49, session_public_id, turn_id)


if __name__ == '__main__':
    unittest.main()
