import hashlib
import json
import unittest

from aporia.infrastructure.db import Connection
from aporia.infrastructure.ontology import (
    AporiaOntologyExperiment,
    AporiaOntologyLedger,
    AporiaOntologyResidualCollector,
    AporiaOntologyTopology,
)


class TestAporiaOntology(unittest.TestCase):
    def test_mdl_holdout_bootstrap_and_duplicate_gate_shadow_promotion(self):
        experiment = AporiaOntologyExperiment()
        shadow = experiment.evaluate(self._proposal([1.0, 0.0], []))
        duplicate = experiment.evaluate(self._proposal([1.0, 0.0], [[1.0, 0.0]]))

        self.assertEqual('shadow', shadow['state'])
        self.assertTrue(shadow['inadequacy_detected'])
        self.assertGreater(shadow['mdl_improvement'], 0.05)
        self.assertGreater(shadow['holdout_improvement'], 0.02)
        self.assertGreater(shadow['bootstrap_lower_95'], 0.0)
        self.assertTrue(shadow['promotion_allowed'])
        self.assertFalse(shadow['automatic_activation_allowed'])
        self.assertEqual('rejected', duplicate['state'])
        self.assertTrue(duplicate['duplicate_detected'])
        self.assertEqual(['ontology_duplicate_detected'], duplicate['reason_codes'])
        self.assertEqual(1, len(shadow['positive_example_commitments']))
        self.assertEqual(1, len(shadow['negative_example_commitments']))

    def test_proposal_rejects_policy_authority_and_missing_example_commitments(self):
        experiment = AporiaOntologyExperiment()
        policy_proposal = self._proposal([1.0, 0.0], [])
        policy_proposal['policy_override'] = 'allow'

        with self.assertRaises(RuntimeError) as ctx:
            experiment.evaluate(policy_proposal)
        self.assertIn('aporia_ontology_proposal_invalid', str(ctx.exception))

        missing_examples = self._proposal([1.0, 0.0], [])
        missing_examples['positive_example_commitments'] = []
        with self.assertRaises(RuntimeError) as ctx:
            experiment.evaluate(missing_examples)
        self.assertIn('aporia_ontology_examples_invalid', str(ctx.exception))

    def test_vietoris_rips_measures_components_filled_triangles_and_one_cycle(self):
        topology = AporiaOntologyTopology()
        square = topology.filtration(
            [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]],
            [0.5, 1.0, 2.0]
        )
        triangle = topology.filtration(
            [[1.0, 0.0], [0.5, 0.8660254038], [-0.5, 0.8660254038]],
            [0.5, 1.0, 2.0]
        )

        self.assertEqual(4, square['filtration'][0]['h0'])
        self.assertEqual(1, square['filtration'][1]['h0'])
        self.assertEqual(1, square['filtration'][1]['h1'])
        self.assertEqual(1, square['filtration'][2]['h0'])
        self.assertEqual(0, square['filtration'][2]['h1'])
        self.assertEqual(1, triangle['filtration'][2]['h0'])
        self.assertEqual(0, triangle['filtration'][2]['h1'])
        self.assertGreater(topology.compare(square, triangle)['betti_curve_l1'], 0.0)

    def test_versioning_rollback_tenant_isolation_and_no_automatic_activation(self):
        conn = self._database()
        ledger = AporiaOntologyLedger(conn, 'ontology-secret')

        conn.begin_transaction()
        first = ledger.propose(49, self._proposal([1.0, 0.0], []), [0.5, 1.0, 2.0], '2026-08-16 00:00:00.000000')
        conn.commit()

        conn.begin_transaction()
        second = ledger.propose(49, self._proposal([0.0, 1.0], []), [0.5, 1.0, 2.0], '2026-08-16 00:00:01.000000')
        conn.commit()

        conn.begin_transaction()
        duplicate = ledger.propose(49, self._proposal([0.0, 1.0], []), [0.5, 1.0, 2.0], '2026-08-16 00:00:02.000000')
        conn.commit()

        self.assertEqual('shadow', first['state'])
        self.assertEqual('shadow', second['state'])
        self.assertEqual('rejected', duplicate['state'])
        self.assertFalse(first['automatic_activation_allowed'])
        self.assertGreater(second['topology_change'], 0.0)
        self.assertEqual(second['version_id'], ledger.current_version(49))
        self.assertIsNone(ledger.current_version(73))
        self.assertEqual(0, int(conn.query("SELECT COUNT(*) FROM aporia_ontology_primitives WHERE state = 'active'").fetchColumn(0)))

        stored_examples = conn.query('SELECT positive_example_commitments_json, negative_example_commitments_json FROM aporia_ontology_primitives ORDER BY id LIMIT 1').fetch()
        self.assertEqual([hashlib.sha256(b'positive-example').hexdigest()], json.loads(str(stored_examples['positive_example_commitments_json'])))
        self.assertEqual([hashlib.sha256(b'negative-example').hexdigest()], json.loads(str(stored_examples['negative_example_commitments_json'])))

        conn.begin_transaction()
        rollback = ledger.rollback(49, '2026-08-16 00:00:03.000000')
        conn.commit()

        self.assertEqual(second['version_id'], rollback['rolled_back_version_id'])
        self.assertEqual(first['version_id'], rollback['restored_version_id'])
        self.assertEqual(first['version_id'], ledger.current_version(49))
        self.assertEqual('rolled_back', conn.query(f"SELECT status FROM aporia_ontology_versions WHERE version_id = '{second['version_id']}'").fetchColumn(0))
        self.assertEqual('aporia.ontology.version.rollback.succeeded', conn.query('SELECT event_name FROM aporia_ontology_events').fetchColumn(0))

        conn.begin_transaction()
        restored = ledger.propose(49, self._proposal([0.0, 1.0], []), [0.5, 1.0, 2.0], '2026-08-16 00:00:04.000000')
        conn.commit()

        self.assertEqual('shadow', restored['state'])
        self.assertEqual(4, restored['version_number'])

    def test_primitive_failure_rolls_back_version_and_head(self):
        conn = self._database()
        ledger = AporiaOntologyLedger(conn, 'ontology-secret')
        conn.exec("CREATE TRIGGER fail_ontology_primitive BEFORE INSERT ON aporia_ontology_primitives BEGIN SELECT RAISE(FAIL, 'ontology failure'); END;")
        conn.begin_transaction()
        try:
            ledger.propose(49, self._proposal([1.0, 0.0], []), [0.5, 1.0], '2026-08-16 00:00:00.000000')
            self.fail('Expected ontology persistence failure.')
        except Exception:
            conn.roll_back()

        self.assertEqual(0, int(conn.query('SELECT COUNT(*) FROM aporia_ontology_heads').fetchColumn(0)))
        self.assertEqual(0, int(conn.query('SELECT COUNT(*) FROM aporia_ontology_versions').fetchColumn(0)))
        self.assertEqual(0, int(conn.query('SELECT COUNT(*) FROM aporia_ontology_primitives').fetchColumn(0)))

    def test_independent_runtime_residuals_create_only_a_shadow_primitive(self):
        conn = self._database()
        collector = AporiaOntologyResidualCollector(conn, 'ontology-secret')
        negative_vector = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        positive_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        for episode in range(1, 9):
            self._record_residual(conn, collector, 49, 'tool.proposed', f'negative-{episode}', negative_vector)

        proposal = None
        for episode in range(1, 161):
            if proposal is not None:
                break
            result = self._record_residual(conn, collector, 49, 'turn.started', f'positive-{episode}', positive_vector)
            proposal = result['proposal']

        self.assertIsInstance(proposal, dict)
        self.assertEqual('shadow', proposal['state'])
        self.assertFalse(proposal['automatic_activation_allowed'])
        self.assertGreaterEqual(int(conn.query("SELECT train_count FROM aporia_ontology_clusters WHERE status = 'shadow'").fetchColumn(0)), 8)
        self.assertGreaterEqual(int(conn.query("SELECT holdout_count FROM aporia_ontology_clusters WHERE status = 'shadow'").fetchColumn(0)), 4)
        self.assertEqual(0, int(conn.query("SELECT COUNT(*) FROM aporia_ontology_primitives WHERE state = 'active'").fetchColumn(0)))
        self.assertEqual(1, int(conn.query("SELECT COUNT(*) FROM aporia_ontology_primitives WHERE state = 'shadow'").fetchColumn(0)))
        self.assertEqual('aporia.ontology.residual.observed', conn.query('SELECT event_name FROM aporia_ontology_residuals ORDER BY id DESC LIMIT 1').fetchColumn(0))
        self.assertEqual('system', conn.query('SELECT stream FROM aporia_ontology_residuals ORDER BY id DESC LIMIT 1').fetchColumn(0))
        self.assertEqual('audit', conn.query('SELECT category FROM aporia_ontology_residuals ORDER BY id DESC LIMIT 1').fetchColumn(0))
        self.assertIsNone(AporiaOntologyLedger(conn, 'ontology-secret').current_version(73))

    def _proposal(self, vector: list[float], existing: list[list[float]]) -> dict:
        return {
            'definition_commitment': hashlib.sha256(json.dumps(vector).encode('utf-8')).hexdigest(),
            'positive_example_commitments': [hashlib.sha256(b'positive-example').hexdigest()],
            'negative_example_commitments': [hashlib.sha256(b'negative-example').hexdigest()],
            'candidate_vector': vector,
            'existing_vectors': existing,
            'training_before': [0.8, 0.7, 0.9, 0.75, 0.85],
            'training_after': [0.3, 0.2, 0.35, 0.25, 0.3],
            'holdout_before': [0.75, 0.7, 0.8, 0.72, 0.78],
            'holdout_after': [0.35, 0.3, 0.4, 0.32, 0.38],
            'complexity_before': 1.0,
            'complexity_after': 1.1,
            'lambda': 0.1,
        }

    def _database(self) -> Connection:
        conn = Connection.in_memory()
        conn.executescript("""
        CREATE TABLE aporia_ontology_heads (tenant_id INTEGER PRIMARY KEY, current_version_id TEXT, version_count INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE aporia_ontology_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_number INTEGER NOT NULL, parent_version_id TEXT, status TEXT NOT NULL, version_commitment TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, version_id), UNIQUE (tenant_id, version_number));
        CREATE TABLE aporia_ontology_primitives (id INTEGER PRIMARY KEY AUTOINCREMENT, primitive_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_id TEXT NOT NULL, definition_commitment TEXT NOT NULL, positive_example_commitments_json TEXT NOT NULL, negative_example_commitments_json TEXT NOT NULL, vector_json TEXT NOT NULL, state TEXT NOT NULL, duplicate_similarity REAL NOT NULL, mdl_before REAL NOT NULL, mdl_after REAL NOT NULL, mdl_improvement REAL NOT NULL, holdout_improvement REAL NOT NULL, bootstrap_lower_95 REAL NOT NULL, topology_before_json TEXT, topology_after_json TEXT NOT NULL, topology_change REAL, automatic_activation_allowed INTEGER NOT NULL, reason_codes_json TEXT NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, primitive_id), UNIQUE (tenant_id, version_id), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_ontology_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ontology_event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, version_id TEXT NOT NULL, target_version_id TEXT, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, ontology_event_id), UNIQUE (tenant_id, operation_id));
        CREATE TABLE aporia_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, session_ref TEXT NOT NULL, event_kind TEXT NOT NULL);
        CREATE TABLE aporia_event_parents (tenant_id INTEGER NOT NULL, child_event_id INTEGER NOT NULL, parent_event_id INTEGER NOT NULL, relation_type TEXT NOT NULL);
        CREATE TABLE aporia_lacuna_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, UNIQUE (tenant_id, id), UNIQUE (tenant_id, run_id));
        CREATE TABLE aporia_ontology_clusters (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, cluster_key TEXT NOT NULL, status TEXT NOT NULL, train_count INTEGER NOT NULL, holdout_count INTEGER NOT NULL, distinct_episode_count INTEGER NOT NULL, last_evaluated_train_count INTEGER NOT NULL, last_evaluated_holdout_count INTEGER NOT NULL, proposed_version_id TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, cluster_id), UNIQUE (tenant_id, cluster_key));
        CREATE TABLE aporia_ontology_residuals (id INTEGER PRIMARY KEY AUTOINCREMENT, residual_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, run_id INTEGER NOT NULL, episode_ref TEXT NOT NULL, cluster_key TEXT NOT NULL, dataset_split TEXT NOT NULL, residual_vector_json TEXT NOT NULL, residual_commitment TEXT NOT NULL, baseline_loss REAL NOT NULL, nearest_similarity REAL NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (tenant_id, residual_id), UNIQUE (tenant_id, run_id), UNIQUE (tenant_id, operation_id));
        """)
        return conn

    def _record_residual(
        self,
        conn: Connection,
        collector: AporiaOntologyResidualCollector,
        tenant_id: int,
        event_kind: str,
        session_ref: str,
        vector: list[float]
    ) -> dict:
        event_id = self._uuid(hashlib.sha256(f'{tenant_id}|{event_kind}|{session_ref}|event'.encode('utf-8')).hexdigest())
        run_id = self._uuid(hashlib.sha256(f'{tenant_id}|{event_kind}|{session_ref}|run'.encode('utf-8')).hexdigest())
        event = conn.prepare('INSERT INTO aporia_events (event_id, tenant_id, session_ref, event_kind) VALUES (?, ?, ?, ?)')
        event.execute([event_id, tenant_id, session_ref, event_kind])
        event_row_id = conn.last_insert_id()
        run = conn.prepare('INSERT INTO aporia_lacuna_runs (run_id, tenant_id) VALUES (?, ?)')
        run.execute([run_id, tenant_id])
        run_row_id = conn.last_insert_id()
        conn.begin_transaction()
        try:
            result = collector.record_and_propose(
                {
                    'id': event_row_id,
                    'event_id': event_id,
                    'tenant_id': tenant_id,
                    'session_ref': session_ref,
                    'event_kind': event_kind,
                },
                run_row_id,
                run_id,
                vector,
                hashlib.sha256(f'{event_id}|input'.encode('utf-8')).hexdigest(),
                '2026-08-16 00:00:00.000000'
            )
            conn.commit()
            return result
        except Exception as exc:
            conn.roll_back()
            raise exc

    def _uuid(self, h: str) -> str:
        s = h[:32].lower()
        s = s[:12] + '5' + s[13:]
        nibble = (int(s[16], 16) & 0x3) | 0x8
        s = s[:16] + f'{nibble:x}' + s[17:]
        return f'{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}'


if __name__ == '__main__':
    unittest.main()
