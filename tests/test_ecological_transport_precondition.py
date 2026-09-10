import unittest
from aporia.infrastructure.db import Connection
from aporia.infrastructure.ecological import AporiaEcologicalTransportPrecondition


class TestAporiaEcologicalTransportPrecondition(unittest.TestCase):
    def setUp(self):
        self.conn = Connection.in_memory()
        self.conn.executescript("""
        CREATE TABLE assistant_runtime_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, owner_user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE assistant_runtime_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, turn_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'accepted', UNIQUE (session_id, turn_id), UNIQUE (session_id, request_id));
        CREATE TABLE aporia_ecological_transport_preconditions_r1 (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, tenant_id INTEGER NOT NULL, session_ref TEXT NOT NULL, turn_ref TEXT NOT NULL, request_ref TEXT NOT NULL, authenticated_websocket INTEGER NOT NULL, session_turn_binding INTEGER NOT NULL, attested_at TEXT NOT NULL, attestation_hash TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, occurred_at TEXT NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, initiator_type TEXT NOT NULL, executor_type TEXT NOT NULL, source_channel TEXT NOT NULL, target_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (tenant_id, turn_ref), UNIQUE (tenant_id, operation_id));
        """)
        self.precondition = AporiaEcologicalTransportPrecondition(self.conn, 'transport-secret', 'staging')

    def test_authenticated_websocket_and_exact_session_turn_are_attested_idempotently(self):
        session_public_id = '11111111-1111-4111-a111-111111111111'
        self.conn.prepare('INSERT INTO assistant_runtime_sessions (public_id, owner_user_id) VALUES (?, 49)').execute([session_public_id])
        session_id = self.conn.last_insert_id()
        self.conn.prepare('INSERT INTO assistant_runtime_runs (session_id, turn_id, request_id) VALUES (?, ?, ?)').execute([session_id, 'turn-a', 'request-a'])

        first = self.precondition.attest(49, session_public_id, 'turn-a', True, 'request-a')
        repeat = self.precondition.attest(49, session_public_id, 'turn-a', True, 'request-a')

        self.assertTrue(first['verified'])
        self.assertTrue(first['new_record'])
        self.assertFalse(repeat['new_record'])
        self.assertEqual(first['attestation_hash'], repeat['attestation_hash'])
        self.assertTrue(self.precondition.verified(49, session_public_id, 'turn-a'))

        row = self.conn.query('SELECT * FROM aporia_ecological_transport_preconditions_r1').fetch()
        self.assertIsNotNone(row)
        self.assertEqual(1, int(self.conn.query('SELECT COUNT(*) FROM aporia_ecological_transport_preconditions_r1').fetchColumn(0)))
        self.assertEqual('aporia.ecological.transport.verified', row['event_name'])
        self.assertEqual(1, int(row['event_version']))
        self.assertEqual('system', row['stream'])
        self.assertEqual('audit', row['category'])
        self.assertEqual('system', row['actor_type'])
        self.assertEqual('user', row['initiator_type'])
        self.assertEqual('service', row['executor_type'])
        self.assertEqual('authenticated_websocket', row['source_channel'])
        self.assertEqual('ecological_turn', row['target_type'])
        self.assertEqual('succeeded', row['outcome'])
        self.assertEqual(64, len(str(row['event_id'])))
        self.assertEqual(64, len(str(row['operation_id'])))
        self.assertEqual(64, len(str(row['session_ref'])))
        self.assertEqual(64, len(str(row['request_ref'])))
        self.assertNotEqual(session_public_id, row['session_ref'])
        self.assertNotIn('session_public_id', row)

    def test_unauthenticated_websocket_is_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.precondition.attest(49, '11111111-1111-4111-a111-111111111111', 'turn-a', False)
        self.assertIn('aporia_ecological_transport_attestation_invalid', str(ctx.exception))

    def test_session_turn_mismatch_is_rejected(self):
        session_public_id = '11111111-1111-4111-a111-111111111111'
        self.conn.prepare('INSERT INTO assistant_runtime_sessions (public_id, owner_user_id) VALUES (?, 49)').execute([session_public_id])
        with self.assertRaises(RuntimeError) as ctx:
            self.precondition.attest(49, session_public_id, 'turn-missing', True, 'request-a')
        self.assertIn('aporia_ecological_session_turn_binding_invalid', str(ctx.exception))

    def test_request_mismatch_is_rejected(self):
        session_public_id = '11111111-1111-4111-a111-111111111111'
        self.conn.prepare('INSERT INTO assistant_runtime_sessions (public_id, owner_user_id) VALUES (?, 49)').execute([session_public_id])
        session_id = self.conn.last_insert_id()
        self.conn.prepare('INSERT INTO assistant_runtime_runs (session_id, turn_id, request_id) VALUES (?, ?, ?)').execute([session_id, 'turn-a', 'request-a'])
        with self.assertRaises(RuntimeError) as ctx:
            self.precondition.attest(49, session_public_id, 'turn-a', True, 'request-b')
        self.assertIn('aporia_ecological_session_turn_binding_invalid', str(ctx.exception))

    def test_technical_failure_returns_sanitized_fail_closed_result(self):
        result = self.precondition.attest_safely(
            49,
            '11111111-1111-4111-a111-111111111111',
            'turn-missing',
            True,
            'request-a'
        )
        self.assertFalse(result['verified'])
        self.assertEqual(['ecological_transport_precondition_unavailable'], result['reason_codes'])
        self.assertEqual(0, int(self.conn.query('SELECT COUNT(*) FROM aporia_ecological_transport_preconditions_r1').fetchColumn(0)))

    def test_scope_outside_staging_pilot_remains_inactive(self):
        result = AporiaEcologicalTransportPrecondition(self.conn, 'transport-secret', 'production').attest(
            49, '11111111-1111-4111-a111-111111111111', 'turn-a', True
        )
        self.assertFalse(result['verified'])
        self.assertEqual(['ecological_transport_scope_inactive'], result['reason_codes'])


if __name__ == '__main__':
    unittest.main()
