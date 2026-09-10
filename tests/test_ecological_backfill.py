import unittest
from aporia.infrastructure.db import Connection
from aporia.application.ecological_backfill import AporiaEcologicalSessionContextBackfill


class TestAporiaEcologicalSessionContextBackfill(unittest.TestCase):
    def test_retroactive_session_linking_is_always_rejected(self):
        conn = Connection.in_memory()
        conn.executescript("""
        CREATE TABLE workspace_profiles (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, profile_id INTEGER NOT NULL, primary_relationship_id INTEGER NULL);
        CREATE TABLE workspace_relationships (id INTEGER PRIMARY KEY, workspace_profile_id INTEGER NOT NULL, relationship_type TEXT NOT NULL, relationship_status TEXT NOT NULL);
        CREATE TABLE assistant_runtime_sessions (id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL, profile_id INTEGER NULL, workspace_profile_id INTEGER NULL, relationship_id INTEGER NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NULL);
        CREATE TABLE assistant_runtime_access_tickets (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, owner_user_id INTEGER NOT NULL, consumed_at TEXT NULL, revoked_at TEXT NULL);
        CREATE TABLE assistant_runtime_access_sessions (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, owner_user_id INTEGER NOT NULL, revoked_at TEXT NULL);
        INSERT INTO workspace_profiles VALUES (200, 49, 100, 300), (201, 49, 101, 301), (202, 49, 102, 302), (203, 50, 103, 303);
        INSERT INTO workspace_relationships VALUES (300, 200, 'lead', 'active'), (301, 201, 'lead', 'archived'), (302, 202, 'personal', 'active'), (303, 203, 'lead', 'active');
        INSERT INTO assistant_runtime_sessions VALUES (1, 49, 100, 200, NULL, 'active', 1, NULL), (2, 49, 101, 201, NULL, 'active', 1, NULL), (3, 49, 102, 202, NULL, 'active', 1, NULL), (4, 50, 103, 203, NULL, 'active', 1, NULL);
        INSERT INTO assistant_runtime_access_tickets VALUES (1, 1, 49, NULL, NULL), (2, 4, 50, NULL, NULL);
        INSERT INTO assistant_runtime_access_sessions VALUES (1, 1, 49, NULL), (2, 4, 50, NULL);
        """)

        with self.assertRaises(RuntimeError) as ctx:
            AporiaEcologicalSessionContextBackfill(conn, 'staging').run()
        self.assertIn('aporia_ecological_retroactive_linking_prohibited', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
