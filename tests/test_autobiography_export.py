import json
import unittest

from aporia.application.autobiography_export import AporiaAutobiographyExport
from aporia.infrastructure.db import Connection


class TestAporiaAutobiographyExport(unittest.TestCase):
    def setUp(self):
        self.conn = Connection.in_memory()
        self.conn.executescript("""
        CREATE TABLE aporia_autobiography_entries (
            id INTEGER PRIMARY KEY, entry_id TEXT, tenant_id INTEGER, run_id INTEGER, causal_event_id TEXT,
            episode_ref TEXT, entry_kind TEXT, evaluability TEXT, ontology_before_commitment TEXT,
            ontology_after_commitment TEXT, transformation_commitment TEXT, lost_distinction_hashes_json TEXT,
            excluded_future_hashes_json TEXT, irrecoverability REAL, causal_efficacy REAL,
            autobiographic_time INTEGER, confidence REAL, reason_codes_json TEXT, policy_hash TEXT,
            payload_hash TEXT, previous_signature TEXT, entry_signature TEXT, schema_version INTEGER,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE aporia_identity_snapshots (
            id INTEGER PRIMARY KEY, snapshot_id TEXT, tenant_id INTEGER, autobiography_entry_id TEXT,
            graph_root TEXT, behavior_root TEXT, commitment_root TEXT, autobiography_root TEXT,
            ontology_root TEXT, graph_count INTEGER, behavior_count INTEGER, commitment_count INTEGER,
            autobiography_count INTEGER, ontology_count INTEGER, fingerprint TEXT, continuity_score REAL,
            model_dependency TEXT, previous_signature TEXT, snapshot_signature TEXT, schema_version INTEGER,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE aporia_identity_commitments (
            id INTEGER PRIMARY KEY, commitment_id TEXT, tenant_id INTEGER, commitment_key TEXT,
            commitment_kind TEXT, status TEXT, evidence_count INTEGER, first_causal_event_id TEXT,
            last_causal_event_id TEXT, committed_at TEXT, schema_version INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE aporia_identity_commitment_evidence (
            id INTEGER PRIMARY KEY, evidence_id TEXT, tenant_id INTEGER, commitment_id INTEGER,
            autobiography_entry_id TEXT, causal_event_id TEXT, evidence_ordinal INTEGER,
            resulting_status TEXT, reason_code TEXT, schema_version INTEGER, created_at TEXT, updated_at TEXT
        );
        """)
        self._insert_tenant(7, 'entry-seven', 'commitment-seven')
        self._insert_tenant(8, 'entry-eight', 'commitment-eight')

    def test_exports_only_the_requested_tenant_with_verified_commitments(self):
        verified_tenant = 0

        def verifier(tenant_id: int) -> bool:
            nonlocal verified_tenant
            verified_tenant = tenant_id
            return True

        service = AporiaAutobiographyExport(self.conn, verifier)
        export = service.export_tenant(7)
        encoded = json.dumps(export)

        self.assertEqual(7, verified_tenant)
        self.assertTrue(export['chain_verified'])
        self.assertEqual('entry-seven', export['autobiography_entries'][0]['entry_id'])
        self.assertEqual('commitment-seven', export['identity_commitments'][0]['commitment_id'])
        self.assertEqual('commitment-seven', export['commitment_evidence'][0]['commitment_id'])
        self.assertNotIn('entry-eight', encoded)
        self.assertNotIn('commitment-eight', encoded)
        for forbidden in ['password', 'token', 'secret', 'plaintext', 'content', 'state', 'prompt']:
            self.assertNotIn(forbidden, export['autobiography_entries'][0])

    def test_rejects_export_when_the_autobiography_chain_is_invalid(self):
        with self.assertRaises(RuntimeError) as ctx:
            AporiaAutobiographyExport(self.conn, lambda tid: False).export_tenant(7)
        self.assertIn('aporia_autobiography_export_chain_invalid', str(ctx.exception))

    def _insert_tenant(self, tenant_id: int, entry_id: str, commitment_id: str):
        h = str(tenant_id % 10) * 64
        entry = self.conn.prepare('INSERT INTO aporia_autobiography_entries VALUES (NULL, ?, ?, 1, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?, 2, ?, ?)')
        entry.execute([entry_id, tenant_id, f'event-{tenant_id}', h, 'negative_observation', 'observed', h, h, '[]', '[]', '["observed"]', h, h, '0' * 64, h, '2026-08-16', '2026-08-16'])
        snapshot = self.conn.prepare('INSERT INTO aporia_identity_snapshots VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 1, 1, ?, 1.0, ?, ?, ?, 1, ?, ?)')
        snapshot.execute([f'snapshot-{tenant_id}', tenant_id, entry_id, h, h, h, h, h, h, 'none', '0' * 64, h, '2026-08-16', '2026-08-16'])
        commitment = self.conn.prepare('INSERT INTO aporia_identity_commitments VALUES (NULL, ?, ?, ?, ?, ?, 3, ?, ?, ?, 1, ?, ?)')
        commitment.execute([commitment_id, tenant_id, h, 'shadow_policy_continuity', 'committed', f'event-first-{tenant_id}', f'event-last-{tenant_id}', '2026-08-16', '2026-08-16', '2026-08-16'])
        numeric_id = self.conn.last_insert_id()
        evidence = self.conn.prepare('INSERT INTO aporia_identity_commitment_evidence VALUES (NULL, ?, ?, ?, ?, ?, 1, ?, ?, 1, ?, ?)')
        evidence.execute([f'evidence-{tenant_id}', tenant_id, numeric_id, entry_id, f'event-{tenant_id}', 'committed', 'commitment_threshold_reached', '2026-08-16', '2026-08-16'])


if __name__ == '__main__':
    unittest.main()
