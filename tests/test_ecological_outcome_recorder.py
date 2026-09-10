import unittest
from aporia.infrastructure.db import Connection
from aporia.infrastructure.ecological import AporiaEcologicalOutcomeRecorder


class TestAporiaEcologicalOutcomeRecorder(unittest.TestCase):
    def test_records_the_same_prior_observation_against_a_real_delayed_outcome_idempotently(self):
        conn = self._database('observation-a', 'observation-a')
        recorder = AporiaEcologicalOutcomeRecorder(conn, 'ecological-secret', 'staging')

        first = recorder.record(49, 900, 2, 'won', '2026-08-22 12:00:00.000000')
        second = recorder.record(49, 900, 2, 'won', '2026-08-22 12:00:00.000000')

        self.assertEqual(1, first['inserted_episodes'])
        self.assertEqual(1, second['idempotent_episodes'])
        self.assertEqual(1, int(conn.query('SELECT COUNT(*) FROM aporia_ecological_episode_outcomes_r2').fetchColumn(0)))

        row = conn.query('SELECT * FROM aporia_ecological_episode_outcomes_r2').fetch()
        self.assertIsNotNone(row)
        self.assertEqual('won', row['outcome_label'])
        self.assertEqual(1, int(row['same_observation']))
        self.assertEqual(1, int(row['predictions_before_outcome']))
        self.assertEqual(0, int(row['runtime_influence']))
        self.assertEqual(0, int(row['external_effect']))
        self.assertEqual(0, int(row['contamination_detected']))
        self.assertEqual(64, len(str(row['relationship_ref'])))
        self.assertEqual(64, len(str(row['session_ref'])))

    def test_rejects_mismatched_twin_observations(self):
        conn = self._database('observation-a', 'observation-b')
        recorder = AporiaEcologicalOutcomeRecorder(conn, 'ecological-secret', 'staging')

        with self.assertRaises(RuntimeError) as ctx:
            recorder.record(49, 900, 2, 'lost', '2026-08-22 12:00:00.000000')
        self.assertIn('aporia_ecological_episode_invalid', str(ctx.exception))

    def test_remains_inactive_outside_account_forty_nine_in_staging(self):
        conn = self._database('observation-a', 'observation-a')
        recorder = AporiaEcologicalOutcomeRecorder(conn, 'ecological-secret', 'production')

        result = recorder.record(49, 900, 2, 'won', '2026-08-22 12:00:00.000000')
        self.assertFalse(result['recorded'])
        self.assertEqual(['ecological_scope_inactive'], result['reason_codes'])

    def _database(self, c0_observation: str, c5_observation: str) -> Connection:
        conn = Connection.in_memory()
        conn.executescript("""
        CREATE TABLE assistant_runtime_sessions (id INTEGER PRIMARY KEY, public_id TEXT NOT NULL, owner_user_id INTEGER NOT NULL, relationship_id INTEGER NULL);
        CREATE TABLE aporia_shadow_episode_lifecycle (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, session_public_id TEXT NOT NULL, turn_ref TEXT NOT NULL, c0_prediction_sealed_at TEXT NULL, c5_prediction_sealed_at TEXT NULL);
        CREATE TABLE aporia_longitudinal_shadow_c0 (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, observation_commitment TEXT NOT NULL, prediction_commitment TEXT NOT NULL, assigned_before_outcome INTEGER NOT NULL, runtime_influence INTEGER NOT NULL, external_effect INTEGER NOT NULL, contamination_detected INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_longitudinal_shadow_c5 (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, turn_ref TEXT NOT NULL, observation_commitment TEXT NOT NULL, prediction_commitment TEXT NOT NULL, assigned_before_outcome INTEGER NOT NULL, runtime_influence INTEGER NOT NULL, external_effect INTEGER NOT NULL, contamination_detected INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_ecological_episode_outcomes_r2 (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, relationship_ref TEXT NOT NULL, relationship_revision INTEGER NOT NULL, turn_ref TEXT NOT NULL, session_ref TEXT NOT NULL, c0_observation_commitment TEXT NOT NULL, c0_prediction_commitment TEXT NOT NULL, c5_observation_commitment TEXT NOT NULL, c5_prediction_commitment TEXT NOT NULL, outcome_label TEXT NOT NULL, outcome_commitment TEXT NOT NULL, outcome_at TEXT NOT NULL, same_observation INTEGER NOT NULL, predictions_before_outcome INTEGER NOT NULL, runtime_influence INTEGER NOT NULL, external_effect INTEGER NOT NULL, contamination_detected INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, operation_id));
        INSERT INTO assistant_runtime_sessions VALUES (1, '11111111-1111-4111-a111-111111111111', 49, 900);
        INSERT INTO aporia_shadow_episode_lifecycle VALUES (1, 49, '11111111-1111-4111-a111-111111111111', 'turn-ref-a', '2026-08-20 10:00:00.000000', '2026-08-20 10:00:00.000000');
        """)
        conn.prepare("INSERT INTO aporia_longitudinal_shadow_c0 VALUES (1, 49, 'turn-ref-a', ?, 'prediction-c0', 1, 0, 0, 0, '2026-08-20 10:00:00.000000')").execute([c0_observation])
        conn.prepare("INSERT INTO aporia_longitudinal_shadow_c5 VALUES (1, 49, 'turn-ref-a', ?, 'prediction-c5', 1, 0, 0, 0, '2026-08-20 10:00:00.000000')").execute([c5_observation])
        return conn


if __name__ == '__main__':
    unittest.main()
