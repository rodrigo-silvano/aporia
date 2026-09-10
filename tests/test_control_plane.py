"""Unit tests for Aporia Independent Control Plane."""
from __future__ import annotations

import unittest
from aporia.config import Environment
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.infrastructure.db import Connection


class TestAporiaIndependentControlPlane(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = Connection()
        self._schema()

    def test_global_and_tenant_switches_are_independent_immediate_and_idempotent(self) -> None:
        controls = AporiaIndependentControlPlane(self.conn, Environment("/tmp", {"APP_ENV": "staging"}))
        self.assertFalse(controls.engaged(49, AporiaIndependentControlPlane.GLOBAL))
        first = controls.set(0, AporiaIndependentControlPlane.GLOBAL, True, 7, "operator_safety_stop")
        second = controls.set(0, AporiaIndependentControlPlane.GLOBAL, True, 7, "operator_safety_stop")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.GLOBAL))
        self.assertEqual(1, int(self.conn.query("SELECT COUNT(*) FROM aporia_independent_control_events").fetch_column()))
        controls.set(0, AporiaIndependentControlPlane.GLOBAL, False, 7, "operator_safe_release")
        controls.set(49, AporiaIndependentControlPlane.TENANT, True, 7, "tenant_safety_stop")
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.TENANT))
        self.assertFalse(controls.engaged(50, AporiaIndependentControlPlane.TENANT))

    def test_environment_switches_work_without_database_state(self) -> None:
        controls = AporiaIndependentControlPlane(self.conn, Environment("/tmp", {
            "APP_ENV": "staging",
            "APORIA_KILL_RUNTIME_INFLUENCE": "1",
            "APORIA_KILL_TENANT_IDS": "49,73",
        }))
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.RUNTIME_INFLUENCE))
        self.assertTrue(controls.engaged(73, AporiaIndependentControlPlane.TENANT))
        self.assertFalse(controls.engaged(74, AporiaIndependentControlPlane.TENANT))
        snapshot = controls.snapshot(49)
        self.assertTrue(snapshot[AporiaIndependentControlPlane.RUNTIME_INFLUENCE])
        self.assertTrue(snapshot[AporiaIndependentControlPlane.TENANT])
        self.assertFalse(snapshot[AporiaIndependentControlPlane.MEMORY_WRITES])

    def test_unavailable_control_store_fails_closed_for_influence_but_not_for_passive_writes(self) -> None:
        conn = Connection()
        controls = AporiaIndependentControlPlane(conn, Environment("/tmp", {"APP_ENV": "staging"}))
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.GLOBAL))
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.RUNTIME_INFLUENCE))
        self.assertTrue(controls.engaged(49, AporiaIndependentControlPlane.MEMORY_WRITES))

    def test_independent_switches_apply_at_every_required_phase_and_survive_reinstantiation(self) -> None:
        environment = Environment("/tmp", {"APP_ENV": "staging"})
        phases = {
            "prepare_turn": AporiaIndependentControlPlane.GLOBAL,
            "streaming": AporiaIndependentControlPlane.RUNTIME_INFLUENCE,
            "after_prediction_seal": AporiaIndependentControlPlane.TENANT,
            "before_outcome": AporiaIndependentControlPlane.RUNTIME_INFLUENCE,
            "memory_write": AporiaIndependentControlPlane.MEMORY_WRITES,
            "ontology_write": AporiaIndependentControlPlane.ONTOLOGY,
            "hirt_advisory": AporiaIndependentControlPlane.HIRT_ADVISORY,
        }

        for phase, switch in phases.items():
            tenant_id = 0 if switch == AporiaIndependentControlPlane.GLOBAL else 49
            controls = AporiaIndependentControlPlane(self.conn, environment)
            engaged = controls.set(tenant_id, switch, True, 7, f"{phase}_engaged")

            self.assertTrue(engaged["changed"])
            self.assertTrue(AporiaIndependentControlPlane(self.conn, environment).engaged(49, switch))

            released = AporiaIndependentControlPlane(self.conn, environment).set(
                tenant_id, switch, False, 7, f"{phase}_released"
            )

            self.assertTrue(released["changed"])
            self.assertFalse(AporiaIndependentControlPlane(self.conn, environment).engaged(49, switch))

        self.assertEqual(14, int(self.conn.query("SELECT COUNT(*) FROM aporia_independent_control_events").fetch_column()))

    def _schema(self) -> None:
        self.conn.execute(
            "CREATE TABLE aporia_independent_control_switches ("
            "tenant_id INTEGER NOT NULL, "
            "switch_name TEXT NOT NULL, "
            "engaged INTEGER NOT NULL, "
            "revision INTEGER NOT NULL, "
            "changed_by_user_id INTEGER NOT NULL, "
            "reason_code TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "UNIQUE (tenant_id, switch_name))"
        )
        self.conn.execute(
            "CREATE TABLE aporia_independent_control_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_id TEXT NOT NULL, "
            "tenant_id INTEGER NOT NULL, "
            "switch_name TEXT NOT NULL, "
            "state_before INTEGER NOT NULL, "
            "state_after INTEGER NOT NULL, "
            "revision INTEGER NOT NULL, "
            "event_name TEXT NOT NULL, "
            "event_version INTEGER NOT NULL, "
            "environment TEXT NOT NULL, "
            "stream TEXT NOT NULL, "
            "category TEXT NOT NULL, "
            "component TEXT NOT NULL, "
            "operation_id TEXT NOT NULL, "
            "actor_type TEXT NOT NULL, "
            "actor_user_id INTEGER NOT NULL, "
            "target_type TEXT NOT NULL, "
            "action_name TEXT NOT NULL, "
            "lifecycle_phase TEXT NOT NULL, "
            "outcome TEXT NOT NULL, "
            "reason_code TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "UNIQUE (tenant_id, event_id), "
            "UNIQUE (tenant_id, operation_id))"
        )


if __name__ == "__main__":
    unittest.main()
