from datetime import datetime, timezone
import hashlib
import hmac
import time
import unittest

from aporia.infrastructure.db import Connection
from aporia.infrastructure.ecological import (
    AporiaProspectiveEcologicalProgram,
    AporiaEcologicalTransportPrecondition,
)


class TestAporiaProspectiveEcologicalProgram(unittest.TestCase):
    def setUp(self):
        self.conn = Connection.in_memory()
        self._schema()
        self.secret = 'prospective-ecological-secret'
        self.program = AporiaProspectiveEcologicalProgram(self.conn, self.secret, 'staging')
        self.transport = AporiaEcologicalTransportPrecondition(self.conn, self.secret, 'staging')

    def test_enrollment_prediction_outcome_and_data_gate_are_strictly_prospective(self):
        enrollment = self.program.enroll(49, 1001, 60)
        self.assertTrue(enrollment['eligible'])
        self.assertEqual(2, enrollment['baseline_quartile'])
        self._shadow_pair(1001)
        seal = self.program.seal_prediction(49, 1001, 'turn-1001', self._session_public_id(1001))
        self.assertTrue(seal['sealed'])
        self.assertEqual(
            self.conn.query('SELECT attestation_hash FROM aporia_ecological_transport_preconditions_r1').fetchColumn(0),
            self.conn.query('SELECT transport_attestation_hash FROM aporia_ecological_predictions_r3').fetchColumn(0)
        )
        self.program.freeze_population_weights(49, 'target-v1', [0.25, 0.25, 0.25, 0.25], 0.10)
        future_dt = datetime.fromtimestamp(time.time() + 3600, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        outcome = self.program.record_terminal(
            49,
            1001,
            'user',
            'relationship_stage_won',
            'won',
            future_dt
        )
        self.assertTrue(outcome['recorded'])
        gate = self.program.data_gate(49)
        self.assertFalse(gate['passed'])
        self.assertEqual('BLOCKED_BY_REAL_DATA', gate['generalization_g1_g6'])
        self.assertEqual('ACCRUING', gate['e_forecast'])
        self.assertEqual('SEALED', gate['g8'])
        self.assertEqual('NOT_ELIGIBLE', gate['e_policy'])
        self.assertFalse(gate['efficacy_revealed'])

    def test_old_or_incomplete_relations_cannot_be_attached_retroactively(self):
        self._shadow_pair(2001)
        result = self.program.seal_prediction(49, 2001, 'turn-2001', self._session_public_id(2001))
        self.assertFalse(result['sealed'])
        self.assertEqual(['prospective_relation_ineligible'], result['reason_codes'])

        incomplete = self.program.enroll(49, 2002, None)
        self.assertFalse(incomplete['eligible'])
        self._shadow_pair(2002)
        self.assertFalse(self.program.seal_prediction(49, 2002, 'turn-2002', self._session_public_id(2002))['sealed'])

    def test_enrollment_is_exactly_idempotent_and_baseline_conflicts_fail_closed(self):
        first = self.program.enroll(49, 2101, 30)
        repeat = self.program.enroll(49, 2101, 30)
        self.assertFalse(repeat['new_record'])
        self.assertEqual(first['relation_ref'], repeat['relation_ref'])
        self.assertEqual(first['baseline_quartile'], repeat['baseline_quartile'])

        with self.assertRaises(RuntimeError) as ctx:
            self.program.enroll(49, 2101, 80)
        self.assertIn('aporia_prospective_enrollment_conflict', str(ctx.exception))

    def test_prediction_pair_created_before_enrollment_cannot_be_linked_later(self):
        self._shadow_pair(2201)
        self.conn.exec("UPDATE aporia_longitudinal_shadow_c0 SET created_at = '2000-01-01 00:00:00.000000'")
        self.conn.exec("UPDATE aporia_longitudinal_shadow_c5 SET created_at = '2000-01-01 00:00:00.000000'")
        self.program.enroll(49, 2201, 30)

        with self.assertRaises(RuntimeError) as ctx:
            self.program.seal_prediction(49, 2201, 'turn-2201', self._session_public_id(2201))
        self.assertIn('aporia_prospective_prediction_before_enrollment', str(ctx.exception))

    def test_outcome_at_or_before_seal_is_rejected(self):
        self.program.enroll(49, 3001, 10)
        self._shadow_pair(3001)
        self.program.seal_prediction(49, 3001, 'turn-3001', self._session_public_id(3001))
        self.program.freeze_population_weights(49, 'target-v1', [0.25, 0.25, 0.25, 0.25], 0.10)

        with self.assertRaises(RuntimeError) as ctx:
            self.program.record_terminal(49, 3001, 'user', 'relationship_stage_lost', 'lost', '2000-01-01 00:00:00.000000')
        self.assertIn('aporia_prospective_late_prediction', str(ctx.exception))

    def test_fixed_gate_and_both_estimands_require_balanced_real_counts(self):
        self.program.freeze_population_weights(49, 'target-balanced-v1', [0.1, 0.2, 0.3, 0.4], 0.10)
        for index in range(100):
            rel_id = 4000 + index
            quartile = index // 25
            score = quartile * 25 + 1
            self.program.enroll(49, rel_id, score)
            self._shadow_pair(rel_id)
            self.program.seal_prediction(49, rel_id, f'turn-{rel_id}', self._session_public_id(rel_id))
            outcome = 'won' if index % 2 == 0 else 'lost'
            outcome_at = datetime.fromtimestamp(time.time() + 3600 + index, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
            self.program.record_terminal(
                49,
                rel_id,
                'user',
                f'relationship_stage_{outcome}',
                outcome,
                outcome_at
            )

        gate = self.program.data_gate(49)
        self.assertTrue(gate['passed'])
        self.assertEqual([25, 25, 25, 25], gate['quartiles'])
        estimands = self.program.estimands(49)
        self.assertIn('balanced_effect', estimands)
        self.assertIn('population_effect', estimands)
        self.assertTrue(estimands['paired_by_relation'])
        self.assertEqual('NOT_ELIGIBLE', estimands['e_policy'])
        self.assertIn('brier_score', estimands['forecast_metrics']['C0'])
        self.assertIn('log_loss', estimands['forecast_metrics']['C5'])
        self.assertEqual(1.0, estimands['forecast_metrics']['C0']['coverage'])

    def test_prediction_retry_returns_stored_seal_and_rejects_conflict(self):
        self.program.enroll(49, 5001, 30)
        self._shadow_pair(5001)
        first = self.program.seal_prediction(49, 5001, 'turn-5001', self._session_public_id(5001))
        repeat = self.program.seal_prediction(49, 5001, 'turn-5001', self._session_public_id(5001))
        self.assertFalse(repeat['new_record'])
        self.assertEqual(first['prediction_seal_hash'], repeat['prediction_seal_hash'])

        turn_ref = hmac.new(self.secret.encode('utf-8'), b'aporia-longitudinal-shadow|49|turn-5001', hashlib.sha256).hexdigest()
        conflict_hash = hashlib.sha256(b'conflict').hexdigest()
        self.conn.prepare('UPDATE aporia_longitudinal_shadow_c5 SET prediction_label = ?, prediction_commitment = ? WHERE tenant_id = 49 AND turn_ref = ?').execute(['proceed', conflict_hash, turn_ref])

        with self.assertRaises(RuntimeError) as ctx:
            self.program.seal_prediction(49, 5001, 'turn-5001', self._session_public_id(5001))
        self.assertIn('aporia_prospective_prediction_conflict', str(ctx.exception))

    def test_prediction_requires_exact_session_relationship_binding(self):
        self.program.enroll(49, 5101, 30)
        self.program.enroll(49, 5102, 30)
        self._shadow_pair(5101)

        with self.assertRaises(RuntimeError) as ctx:
            self.program.seal_prediction(49, 5102, 'turn-5101', self._session_public_id(5101))
        self.assertIn('aporia_prospective_session_relationship_mismatch', str(ctx.exception))

    def test_prediction_requires_authenticated_websocket_precondition(self):
        self.program.enroll(49, 5151, 30)
        self._shadow_pair(5151, attest=False)

        with self.assertRaises(RuntimeError) as ctx:
            self.program.seal_prediction(49, 5151, 'turn-5151', self._session_public_id(5151))
        self.assertIn('aporia_ecological_transport_precondition_missing', str(ctx.exception))

    def test_terminal_outcome_is_exactly_idempotent_and_conflicts_fail_closed(self):
        self.program.enroll(49, 5201, 30)
        self._shadow_pair(5201)
        self.program.seal_prediction(49, 5201, 'turn-5201', self._session_public_id(5201))
        self.program.freeze_population_weights(49, 'target-v1', [0.25, 0.25, 0.25, 0.25], 0.10)
        outcome_at = datetime.fromtimestamp(time.time() + 3600, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        first = self.program.record_terminal(49, 5201, 'user', 'relationship_stage_won', 'won', outcome_at)
        repeat = self.program.record_terminal(49, 5201, 'user', 'relationship_stage_won', 'won', outcome_at)
        self.assertFalse(repeat['new_record'])
        self.assertEqual(first['outcome_commitment'], repeat['outcome_commitment'])

        with self.assertRaises(RuntimeError) as ctx:
            self.program.record_terminal(49, 5201, 'user', 'relationship_stage_lost', 'lost', outcome_at)
        self.assertIn('aporia_prospective_outcome_conflict', str(ctx.exception))

    def test_censoring_and_terminal_outcome_are_mutually_exclusive(self):
        self.program.enroll(49, 5301, 30)
        self._shadow_pair(5301)
        self.program.seal_prediction(49, 5301, 'turn-5301', self._session_public_id(5301))
        censored_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        first_censor = self.program.censor(49, 5301, 'followup_window_expired', censored_at)
        repeat_censor = self.program.censor(49, 5301, 'followup_window_expired', censored_at)
        self.assertTrue(first_censor['changed'])
        self.assertFalse(repeat_censor['changed'])

        with self.assertRaises(RuntimeError) as ctx:
            self.program.censor(49, 5301, 'manual_exclusion_requested', censored_at)
        self.assertIn('aporia_prospective_censor_conflict', str(ctx.exception))

        future_dt = datetime.fromtimestamp(time.time() + 3600, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        with self.assertRaises(RuntimeError) as ctx:
            self.program.record_terminal(49, 5301, 'user', 'relationship_stage_won', 'won', future_dt)
        self.assertIn('aporia_prospective_outcome_after_censor', str(ctx.exception))

        self.program.enroll(49, 5302, 30)
        self._shadow_pair(5302)
        self.program.seal_prediction(49, 5302, 'turn-5302', self._session_public_id(5302))
        self.program.freeze_population_weights(49, 'target-v1', [0.25, 0.25, 0.25, 0.25], 0.10)
        self.program.record_terminal(49, 5302, 'user', 'relationship_stage_won', 'won', future_dt)

        with self.assertRaises(RuntimeError) as ctx:
            self.program.censor(49, 5302, 'followup_window_expired', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
        self.assertIn('aporia_prospective_censor_after_outcome', str(ctx.exception))

    def test_only_one_pre_outcome_design_can_be_frozen(self):
        first = self.program.freeze_population_weights(49, 'design-v1', [0.25, 0.25, 0.25, 0.25], 0.10)
        repeat = self.program.freeze_population_weights(49, 'design-v1', [0.25, 0.25, 0.25, 0.25], 0.10)
        self.assertEqual(first['design_commitment'], repeat['design_commitment'])
        self.assertFalse(repeat['new_record'])

        with self.assertRaises(RuntimeError) as ctx:
            self.program.freeze_population_weights(49, 'design-v2', [0.1, 0.2, 0.3, 0.4], 0.20)
        self.assertIn('aporia_ecological_design_conflict', str(ctx.exception))

    def _shadow_pair(self, relationship_id: int, attest: bool = True):
        turn_id = f'turn-{relationship_id}'
        turn_ref = hmac.new(self.secret.encode('utf-8'), f'aporia-longitudinal-shadow|49|{turn_id}'.encode('utf-8'), hashlib.sha256).hexdigest()
        created_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        observation = hashlib.sha256(f'observation|{relationship_id}'.encode('utf-8')).hexdigest()
        c0 = hashlib.sha256(f'C0|{relationship_id}'.encode('utf-8')).hexdigest()
        c5 = hashlib.sha256(f'C5|{relationship_id}'.encode('utf-8')).hexdigest()
        self.conn.prepare('INSERT INTO aporia_longitudinal_shadow_c0 (tenant_id, turn_ref, observation_commitment, prediction_label, prediction_commitment, created_at) VALUES (49, ?, ?, ?, ?, ?)').execute([turn_ref, observation, 'proceed', c0, created_at])
        self.conn.prepare('INSERT INTO aporia_longitudinal_shadow_c5 (tenant_id, turn_ref, observation_commitment, prediction_label, prediction_commitment, created_at) VALUES (49, ?, ?, ?, ?, ?)').execute([turn_ref, observation, 'guard' if relationship_id % 2 == 0 else 'proceed', c5, created_at])
        session_public_id = self._session_public_id(relationship_id)
        self.conn.prepare('INSERT INTO assistant_runtime_sessions (public_id, owner_user_id, relationship_id) VALUES (?, 49, ?)').execute([session_public_id, relationship_id])
        session_id = self.conn.last_insert_id()
        request_id = f'request-{relationship_id}'
        self.conn.prepare('INSERT INTO assistant_runtime_runs (session_id, turn_id, request_id, status) VALUES (?, ?, ?, ?)').execute([session_id, turn_id, request_id, 'accepted'])
        if attest:
            self.transport.attest(49, session_public_id, turn_id, True, request_id)
        self.conn.prepare('INSERT INTO aporia_shadow_episode_lifecycle (tenant_id, session_public_id, turn_ref) VALUES (49, ?, ?)').execute([session_public_id, turn_ref])

    def _session_public_id(self, relationship_id: int) -> str:
        return f'00000000-0000-4000-8000-{relationship_id:012d}'

    def _schema(self):
        self.conn.executescript("""
        CREATE TABLE aporia_obstruction_measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, corrected_self_score REAL NOT NULL);
        CREATE TABLE aporia_longitudinal_shadow_c0 (tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, observation_commitment TEXT NOT NULL, prediction_label TEXT NOT NULL, prediction_commitment TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_longitudinal_shadow_c5 (tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, observation_commitment TEXT NOT NULL, prediction_label TEXT NOT NULL, prediction_commitment TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE assistant_runtime_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, owner_user_id INTEGER NOT NULL, relationship_id INTEGER NULL, status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE assistant_runtime_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, turn_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL, UNIQUE (session_id, turn_id), UNIQUE (session_id, request_id));
        CREATE TABLE aporia_shadow_episode_lifecycle (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, session_public_id TEXT NOT NULL, turn_ref TEXT NOT NULL, UNIQUE (tenant_id, turn_ref));
        CREATE TABLE aporia_ecological_transport_preconditions_r1 (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, session_ref TEXT NOT NULL, turn_ref TEXT NOT NULL, request_ref TEXT NOT NULL, authenticated_websocket INTEGER NOT NULL, session_turn_binding INTEGER NOT NULL, attested_at TEXT NOT NULL, attestation_hash TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, occurred_at TEXT NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, initiator_type TEXT NOT NULL, executor_type TEXT NOT NULL, source_channel TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, turn_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_ecological_relations_r3 (id INTEGER PRIMARY KEY AUTOINCREMENT, relation_ref TEXT NOT NULL, tenant_id INTEGER NOT NULL, relationship_id INTEGER NOT NULL, eligibility_version TEXT NOT NULL, enrolled_at TEXT NOT NULL, baseline_quartile INTEGER NULL, quartile_model_version TEXT NOT NULL, c0_lineage TEXT NOT NULL, c5_lineage TEXT NOT NULL, c0_state_hash TEXT NOT NULL, c5_state_hash TEXT NOT NULL, enrollment_status TEXT NOT NULL, provenance_complete INTEGER NOT NULL, censoring_reason TEXT NULL, censored_at TEXT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, relation_ref), UNIQUE (tenant_id, relationship_id), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_ecological_predictions_r3 (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, relation_ref TEXT NOT NULL, turn_ref TEXT NOT NULL, observation_commitment TEXT NOT NULL, c0_prediction_label TEXT NOT NULL, c0_win_probability REAL NOT NULL, c0_prediction_commitment TEXT NOT NULL, c5_prediction_label TEXT NOT NULL, c5_win_probability REAL NOT NULL, c5_prediction_commitment TEXT NOT NULL, prediction_sealed_at TEXT NOT NULL, prediction_seal_hash TEXT NOT NULL, transport_attestation_hash TEXT NULL, same_observation INTEGER NOT NULL, separate_stores INTEGER NOT NULL, runtime_influence INTEGER NOT NULL, external_effect INTEGER NOT NULL, contamination_detected INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, relation_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_ecological_terminal_outcomes_r3 (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, relation_ref TEXT NOT NULL, actual_action_source TEXT NOT NULL, actual_action TEXT NOT NULL, terminal_definition TEXT NOT NULL, terminal_outcome TEXT NOT NULL, outcome_timestamp TEXT NOT NULL, censoring_reason TEXT NULL, outcome_commitment TEXT NOT NULL, adjudication_blinded_to_arm INTEGER NOT NULL, provenance_complete INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, relation_ref), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_ecological_population_weights_r3 (tenant_id INTEGER NOT NULL PRIMARY KEY, weight_version TEXT NOT NULL, weights_json TEXT NOT NULL, loss_margin REAL NOT NULL, design_commitment TEXT NOT NULL, frozen_at TEXT NOT NULL, UNIQUE (tenant_id, design_commitment));
        """)


if __name__ == '__main__':
    unittest.main()
