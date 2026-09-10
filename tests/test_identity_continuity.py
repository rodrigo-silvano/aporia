import hashlib
import json
import re
import unittest

from aporia.infrastructure.autobiography import (
    AporiaAutobiographyEvidenceInstrument,
    AporiaNegativeAutobiographyEvaluator,
)
from aporia.infrastructure.db import Connection
from aporia.infrastructure.identity_continuity import AporiaIdentityContinuity


class TestAporiaIdentityContinuity(unittest.TestCase):
    def test_negative_autobiography_detects_lost_distinctions_and_excluded_futures(self):
        hashes = [hashlib.sha256(val.encode('utf-8')).hexdigest() for val in ['pair', 'future-a', 'future-b']]
        result = AporiaNegativeAutobiographyEvaluator().evaluate(
            [{
                'pair_hash': hashes[0],
                'before_left': [1.0, 0.0],
                'before_right': [0.0, 1.0],
                'after_left': [0.5, 0.5],
                'after_right': [0.5, 0.5],
            }],
            [hashes[1], hashes[2]],
            [hashes[2]]
        )

        self.assertEqual('observed', result['evaluability'])
        self.assertEqual([hashes[0]], result['lost_distinction_hashes'])
        self.assertEqual([hashes[1]], result['excluded_future_hashes'])
        self.assertEqual(1.0, float(result['distinction_loss_rate']))
        self.assertEqual(0.5, float(result['future_exclusion_rate']))
        self.assertIsNone(result['irrecoverability'])
        self.assertIsNone(result['causal_efficacy'])
        self.assertIsNone(result['confidence'])
        self.assertEqual({'delta_high': 0.5, 'delta_low': 0.15}, result['thresholds'])

    def test_negative_autobiography_remains_not_evaluable_without_instrumentation(self):
        result = AporiaNegativeAutobiographyEvaluator().evaluate([], [], [])

        self.assertEqual('not_evaluable', result['evaluability'])
        self.assertEqual([], result['lost_distinction_hashes'])
        self.assertEqual([], result['excluded_future_hashes'])
        self.assertEqual(['hypotheses_and_futures_not_instrumented'], result['reason_codes'])

    def test_runtime_evidence_uses_supported_transitions_and_structural_futures(self):
        conn = Connection.in_memory()
        conn.executescript("""
        CREATE TABLE aporia_events (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, event_kind TEXT NOT NULL);
        CREATE TABLE aporia_event_parents (tenant_id INTEGER NOT NULL, child_event_id INTEGER NOT NULL, parent_event_id INTEGER NOT NULL, relation_type TEXT NOT NULL);
        """)

        for _ in range(3):
            self._insert_transition(conn, 49, 'turn.started', 'turn.completed')
            self._insert_transition(conn, 49, 'tool.proposed', 'effect.started')

        parent_id = self._insert_event(conn, 49, 'effect.started')
        current_id = self._insert_event(conn, 49, 'effect.completed')
        edge = conn.prepare("INSERT INTO aporia_event_parents VALUES (?, ?, ?, 'session_predecessor')")
        edge.execute([49, current_id, parent_id])

        instrument = AporiaAutobiographyEvidenceInstrument(conn, 'evidence-secret')
        evidence = instrument.observe({'tenant_id': 49, 'id': current_id, 'event_kind': 'effect.completed'})
        result = AporiaNegativeAutobiographyEvaluator().evaluate(
            evidence['hypothesis_pairs'],
            evidence['futures_before'],
            evidence['futures_after']
        )

        self.assertTrue(len(evidence['hypothesis_pairs']) > 0)
        self.assertEqual('effect.started', evidence['predecessor_event_kind'])
        self.assertEqual('observed', result['evaluability'])
        self.assertTrue(len(result['excluded_future_hashes']) > 0)
        for commitment in evidence['futures_before'] + evidence['futures_after']:
            self.assertRegex(commitment, r'^[0-9a-f]{64}$')
        self.assertNotIn('effect.', json.dumps(evidence['futures_before']))
        self.assertEqual(evidence, instrument.observe({'tenant_id': 49, 'id': current_id, 'event_kind': 'effect.completed'}))

    def test_identity_fingerprint_is_model_independent_and_one_experience_is_bounded(self):
        identity = AporiaIdentityContinuity()
        state = self._state('base')
        before = identity.fingerprint(state)
        after_state = identity.advance(state, 'autobiography', hashlib.sha256(b'one-interaction').hexdigest())
        after = identity.fingerprint(after_state)
        similarity = identity.similarity(state, after_state)

        self.assertEqual(before, identity.fingerprint(state))
        self.assertEqual('none', before['model_dependency'])
        self.assertNotEqual(before['fingerprint'], after['fingerprint'])
        self.assertGreaterEqual(similarity['score'], 0.75)
        self.assertEqual({
            'graph': 0.25,
            'behavior': 0.25,
            'commitment': 0.25,
            'autobiography': 0.25,
        }, similarity['weights'])

    def test_forks_start_equal_diverge_and_merge_preserves_both_histories(self):
        identity = AporiaIdentityContinuity()
        base = self._state('base')
        left = identity.fork(base)
        right = identity.fork(base)
        self.assertEqual(identity.fingerprint(left), identity.fingerprint(right))

        left_commitment = hashlib.sha256(b'left-experience').hexdigest()
        right_commitment = hashlib.sha256(b'right-experience').hexdigest()
        left = identity.advance(left, 'autobiography', left_commitment)
        right = identity.advance(right, 'autobiography', right_commitment)
        self.assertNotEqual(identity.fingerprint(left)['fingerprint'], identity.fingerprint(right)['fingerprint'])

        merge = identity.merge(base, left, right)
        self.assertIn(left_commitment, merge['state']['autobiography'])
        self.assertIn(right_commitment, merge['state']['autobiography'])
        self.assertEqual([left_commitment], merge['divergences']['autobiography']['left_only'])
        self.assertEqual([right_commitment], merge['divergences']['autobiography']['right_only'])
        self.assertTrue(merge['divergences']['autobiography']['conflict'])

    def test_identity_rejects_model_metadata_and_non_commitment_content(self):
        state = self._state('base')
        state['model_id'] = ['gpt']

        with self.assertRaises(RuntimeError) as ctx:
            AporiaIdentityContinuity().fingerprint(state)
        self.assertIn('aporia_identity_state_invalid', str(ctx.exception))

    def _state(self, prefix: str) -> dict:
        return {
            'graph': [hashlib.sha256(f'{prefix}-graph'.encode('utf-8')).hexdigest()],
            'behavior': [hashlib.sha256(f'{prefix}-behavior'.encode('utf-8')).hexdigest()],
            'commitment': [hashlib.sha256(f'{prefix}-commitment'.encode('utf-8')).hexdigest()],
            'autobiography': [hashlib.sha256(f'{prefix}-autobiography'.encode('utf-8')).hexdigest()],
            'ontology': [hashlib.sha256(f'{prefix}-ontology'.encode('utf-8')).hexdigest()],
        }

    def _insert_transition(self, conn: Connection, tenant_id: int, parent_kind: str, child_kind: str):
        parent_id = self._insert_event(conn, tenant_id, parent_kind)
        child_id = self._insert_event(conn, tenant_id, child_kind)
        stmt = conn.prepare("INSERT INTO aporia_event_parents VALUES (?, ?, ?, 'session_predecessor')")
        stmt.execute([tenant_id, child_id, parent_id])

    def _insert_event(self, conn: Connection, tenant_id: int, event_kind: str) -> int:
        stmt = conn.prepare('INSERT INTO aporia_events (tenant_id, event_kind) VALUES (?, ?)')
        stmt.execute([tenant_id, event_kind])
        return conn.last_insert_id()


if __name__ == '__main__':
    unittest.main()
