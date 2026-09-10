"""Unit tests for Aporia Generalization Human Patterns and Real Pilot Evaluation."""
from __future__ import annotations

import unittest
from aporia.infrastructure.db import Connection
from aporia.infrastructure.generalization import AporiaGeneralizationHumanPatterns
from aporia.infrastructure.pilot_evaluation import AporiaRealPilotEvaluation


class TestAporiaGeneralizationHumanPatterns(unittest.TestCase):
    def test_produces_only_k_anonymous_non_personal_quartiles(self) -> None:
        conn = self._database()
        for index in range(100):
            profile = 1000 + index
            relationship = 2000 + index
            score = (index % 4) * 25 + 12
            stage = "won" if (index % 3 == 0) else "lost"
            conn.prepare("INSERT INTO workspace_profiles (id, user_id) VALUES (?, ?)").execute([
                profile, 10 + (index % 5)
            ])
            conn.prepare(
                "INSERT INTO workspace_relationships "
                "(id, workspace_profile_id, relationship_type, stage, score, created_at, updated_at, won_at, lost_at) "
                "VALUES (?, ?, 'lead', ?, ?, '2026-01-01 00:00:00', '2026-01-11 00:00:00', ?, ?)"
            ).execute([
                relationship,
                profile,
                stage,
                score,
                "2026-01-11 00:00:00" if stage == "won" else None,
                "2026-01-11 00:00:00" if stage == "lost" else None,
            ])
            conn.prepare(
                "INSERT INTO user_conversations (user_id, relationship_id, last_participant_message_at) "
                "VALUES (?, ?, ?)"
            ).execute([
                10 + (index % 5),
                relationship,
                "2026-01-05 00:00:00" if index % 2 == 0 else None,
            ])

        result = AporiaGeneralizationHumanPatterns(conn, "staging").aggregate()

        self.assertEqual(2, result["schema_version"])
        self.assertEqual(100, result["source_episodes"])
        self.assertFalse(result["personal_data_exported"])
        self.assertEqual([25, 25, 25, 25], [q["sample_count"] for q in result["quartiles"]])
        self.assertEqual([0, 1, 2, 3], [q["quartile"] for q in result["quartiles"]])
        self.assertEqual([1, 0, 1, 0], [q["response_rate"] for q in result["quartiles"]])
        self.assertNotIn("tenant_id", result)
        self.assertNotIn("relationship_id", result)

    def test_rejects_an_underpowered_human_sample(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            AporiaGeneralizationHumanPatterns(self._database(), "staging").aggregate()
        self.assertIn("generalization_r2_human_sample_insufficient", str(ctx.exception))

    def _database(self) -> Connection:
        conn = Connection()
        conn.execute("CREATE TABLE workspace_profiles (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)")
        conn.execute(
            "CREATE TABLE workspace_relationships ("
            "id INTEGER PRIMARY KEY, workspace_profile_id INTEGER NOT NULL, relationship_type TEXT NOT NULL, "
            "stage TEXT NOT NULL, score INTEGER NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "won_at TEXT NULL, lost_at TEXT NULL)"
        )
        conn.execute(
            "CREATE TABLE user_conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, relationship_id INTEGER NULL, "
            "last_participant_message_at TEXT NULL)"
        )
        return conn


class TestAporiaRealPilotEvaluation(unittest.TestCase):
    def test_keeps_pilot_not_evaluable_until_both_arms_have_enough_real_feedback(self) -> None:
        conn = self._database()
        self._insert(conn, "C0", 7, 5, 100.0, 100.0)
        self._insert(conn, "C5", 8, 7, 105.0, 120.0)

        summary = AporiaRealPilotEvaluation(conn).summarize(49)

        self.assertEqual("not_evaluable", summary["evaluability"])
        self.assertEqual("collecting", summary["status"])
        self.assertEqual(["insufficient_real_user_feedback"], summary["reason_codes"])

    def test_evaluates_observed_feedback_and_preserves_negative_result(self) -> None:
        conn = self._database()
        self._insert(conn, "C0", 8, 7, 100.0, 100.0)
        self._insert(conn, "C5", 8, 6, 110.0, 120.0)

        summary = AporiaRealPilotEvaluation(conn).summarize(49)

        self.assertEqual("observed", summary["evaluability"])
        self.assertEqual("negative_result", summary["status"])
        self.assertLess(summary["helpfulness_absolute_effect"], 0.0)
        self.assertEqual(1.2, summary["cost_overhead_ratio"])
        self.assertFalse(summary["gates"]["helpfulness_improved"])
        self.assertEqual(1.0, summary["arms"]["C0"]["completion_without_critical_regression_rate"])
        self.assertEqual(1.0, summary["arms"]["C5"]["completion_without_critical_regression_rate"])

    def test_critical_regression_stops_pilot_before_minimum_sample(self) -> None:
        conn = self._database()
        self._insert(conn, "C0", 1, 1, 100.0, 100.0, True)
        self._insert(conn, "C5", 1, 1, 100.0, 100.0)

        summary = AporiaRealPilotEvaluation(conn).summarize(49)

        self.assertEqual("stopped", summary["status"])
        self.assertFalse(summary["gates"]["no_critical_regressions"])

    def test_counts_repeated_turn_feedback_as_one_session_observation(self) -> None:
        conn = self._database()
        self._insert(conn, "C0", 16, 12, 100.0, 100.0, False, 2)
        self._insert(conn, "C5", 16, 14, 110.0, 120.0, False, 2)

        summary = AporiaRealPilotEvaluation(conn).summarize(49)

        self.assertEqual("real_session", summary["analysis_unit"])
        self.assertEqual("first_observed_feedback_per_session", summary["feedback_selection"])
        self.assertEqual(8, summary["arms"]["C0"]["session_count"])
        self.assertEqual(8, summary["arms"]["C0"]["observed_feedback_count"])
        self.assertEqual(16, summary["arms"]["C0"]["outcome_count"])

    def _database(self) -> Connection:
        conn = Connection()
        conn.execute(
            "CREATE TABLE aporia_experiment_assignments ("
            "assignment_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, experiment_key TEXT NOT NULL, "
            "arm TEXT NOT NULL, assignment_commitment TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE aporia_advisory_outcomes ("
            "advisory_outcome_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, assignment_id TEXT NOT NULL, "
            "feedback_correct INTEGER, critical_regression INTEGER NOT NULL, latency_ms INTEGER NOT NULL, "
            "cost_units REAL NOT NULL, created_at TEXT NOT NULL)"
        )
        return conn

    def _insert(
        self,
        conn: Connection,
        arm: str,
        count: int,
        helpful: int,
        latency: float,
        cost: float,
        critical_regression: bool = False,
        session_group_size: int = 1,
    ) -> None:
        assignment = conn.prepare("INSERT INTO aporia_experiment_assignments VALUES (?, 49, ?, ?, ?)")
        outcome = conn.prepare("INSERT INTO aporia_advisory_outcomes VALUES (?, 49, ?, ?, ?, ?, ?, ?)")
        for index in range(count):
            assignment_id = f"{arm}-{index}"
            assignment.execute([
                assignment_id,
                "aporia_real_pilot_v1",
                arm,
                f"{arm}-session-{index // session_group_size}",
            ])
            outcome.execute([
                f"outcome-{assignment_id}",
                assignment_id,
                1 if index < helpful else 0,
                1 if (critical_regression and index == 0) else 0,
                latency,
                cost,
                f"2026-08-16 12:00:{index:02d}",
            ])


if __name__ == "__main__":
    unittest.main()
