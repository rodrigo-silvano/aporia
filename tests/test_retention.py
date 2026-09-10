from datetime import datetime, timezone
import unittest

from aporia.infrastructure.db import Connection
from aporia.infrastructure.experiments import AporiaRetention


class TestAporiaRetention(unittest.TestCase):
    def setUp(self):
        self.conn = Connection.in_memory()
        self.conn.executescript("""
        CREATE TABLE aporia_tenant_clocks (tenant_id INTEGER PRIMARY KEY, updated_at TEXT NOT NULL);
        CREATE TABLE aporia_events (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_autobiography_entries (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_identity_commitments (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_effect_contracts (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_effect_outcomes (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_product_state_items (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_product_state_snapshots (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_product_context_exposures (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_product_runtime_outcomes (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE aporia_product_lacuna_one_shots (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, created_at TEXT NOT NULL);
        """)

    def test_purges_only_completely_inactive_tenant_datasets(self):
        self._insert_tenant(41, '2025-01-01 00:00:00.000000')
        self._insert_tenant(42, '2026-08-10 00:00:00.000000')

        now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        result = AporiaRetention(self.conn).prune_inactive_tenants(90, 100, now)

        self.assertEqual(1, result['attempted'])
        self.assertEqual(1, result['succeeded'])
        self.assertEqual(0, result['failed'])
        self.assertEqual(1, result['purged_tenants'])
        self.assertEqual(11, result['deleted_rows'])
        self.assertEqual(0, self._count_tenant_rows(41))
        self.assertEqual(11, self._count_tenant_rows(42))

    def test_rolls_back_the_whole_tenant_when_any_table_cannot_be_purged(self):
        self._insert_tenant(51, '2025-01-01 00:00:00.000000')
        self.conn.exec("CREATE TRIGGER prevent_aporia_event_delete BEFORE DELETE ON aporia_events WHEN OLD.tenant_id = 51 BEGIN SELECT RAISE(ABORT, 'retention_failure'); END;")

        now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        result = AporiaRetention(self.conn).prune_inactive_tenants(90, 100, now)

        self.assertEqual(1, result['attempted'])
        self.assertEqual(0, result['succeeded'])
        self.assertEqual(1, result['failed'])
        self.assertEqual(['aporia_tenant_retention_failed'], result['reason_codes'])
        self.assertEqual(11, self._count_tenant_rows(51))

    def test_rejects_unsafe_retention_configuration(self):
        with self.assertRaises(ValueError) as ctx:
            AporiaRetention(self.conn).prune_inactive_tenants(7, 0)
        self.assertIn('aporia_retention_configuration_invalid', str(ctx.exception))

    def _insert_tenant(self, tenant_id: int, updated_at: str):
        clock = self.conn.prepare('INSERT INTO aporia_tenant_clocks (tenant_id, updated_at) VALUES (?, ?)')
        clock.execute([tenant_id, updated_at])
        for table in ['aporia_events', 'aporia_autobiography_entries', 'aporia_identity_commitments', 'aporia_effect_contracts', 'aporia_effect_outcomes', 'aporia_product_state_items', 'aporia_product_state_snapshots', 'aporia_product_context_exposures', 'aporia_product_runtime_outcomes', 'aporia_product_lacuna_one_shots']:
            stmt = self.conn.prepare(f'INSERT INTO {table} (tenant_id, created_at) VALUES (?, ?)')
            stmt.execute([tenant_id, updated_at])

    def _count_tenant_rows(self, tenant_id: int) -> int:
        count = 0
        for table in ['aporia_tenant_clocks', 'aporia_events', 'aporia_autobiography_entries', 'aporia_identity_commitments', 'aporia_effect_contracts', 'aporia_effect_outcomes', 'aporia_product_state_items', 'aporia_product_state_snapshots', 'aporia_product_context_exposures', 'aporia_product_runtime_outcomes', 'aporia_product_lacuna_one_shots']:
            stmt = self.conn.prepare(f'SELECT COUNT(*) FROM {table} WHERE tenant_id = ?')
            stmt.execute([tenant_id])
            count += int(stmt.fetchColumn(0))
        return count


if __name__ == '__main__':
    unittest.main()
