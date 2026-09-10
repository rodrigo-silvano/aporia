"""
Aporia Real Pilot Evaluation.
Summarizes pilot experiment performance, evaluating real-user feedback per session
with Wilson score intervals and stop/gate rules.
"""
from __future__ import annotations

import math
from typing import Any
from aporia.infrastructure.db import Connection


class AporiaRealPilotEvaluation:
    def __init__(self, pdo: Connection | Any) -> None:
        self.pdo = pdo if isinstance(pdo, Connection) else Connection(raw_conn=pdo)

    def summarize(self, tenant_id: int, minimum_observed_per_arm: int = 8) -> dict[str, Any]:
        if tenant_id < 1 or minimum_observed_per_arm < 8 or minimum_observed_per_arm > 10000:
            raise ValueError("aporia_real_pilot_evaluation_invalid")

        stmt = self.pdo.prepare("""
            SELECT a.arm, a.assignment_commitment, o.feedback_correct, o.critical_regression,
                   o.latency_ms, o.cost_units, o.created_at, o.advisory_outcome_id
            FROM aporia_experiment_assignments a
            INNER JOIN aporia_advisory_outcomes o
               ON o.tenant_id = a.tenant_id AND o.assignment_id = a.assignment_id
            WHERE a.tenant_id = ? AND a.experiment_key = 'aporia_real_pilot_v1' AND a.arm IN ('C0','C5')
            ORDER BY a.arm, a.assignment_commitment, o.created_at, o.advisory_outcome_id
        """)
        stmt.execute([tenant_id])
        rows = stmt.fetchAll()

        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = f"{row['arm']}|{row['assignment_commitment']}"
            if key not in sessions:
                sessions[key] = {
                    "arm": str(row["arm"]),
                    "outcome_count": 0,
                    "first_feedback": None,
                    "critical_regression": False,
                    "latency_sum": 0.0,
                    "cost_sum": 0.0,
                }
            sessions[key]["outcome_count"] += 1
            if sessions[key]["first_feedback"] is None and row.get("feedback_correct") is not None:
                sessions[key]["first_feedback"] = int(row["feedback_correct"])
            sessions[key]["critical_regression"] = sessions[key]["critical_regression"] or (int(row.get("critical_regression") or 0) == 1)
            sessions[key]["latency_sum"] += float(row.get("latency_ms") or 0.0)
            sessions[key]["cost_sum"] += float(row.get("cost_units") or 0.0)

        arms: dict[str, dict[str, Any]] = {}
        for arm in ("C0", "C5"):
            arm_sessions = [s for s in sessions.values() if s["arm"] == arm]
            session_count = len(arm_sessions)
            observed = sum(1 for s in arm_sessions if s["first_feedback"] is not None)
            helpful = sum(1 for s in arm_sessions if s["first_feedback"] == 1)
            critical = sum(1 for s in arm_sessions if s["critical_regression"])
            rate = (helpful / observed) if observed > 0 else None
            arms[arm] = {
                "session_count": session_count,
                "outcome_count": sum(s["outcome_count"] for s in arm_sessions),
                "observed_feedback_count": observed,
                "helpful_count": helpful,
                "helpful_rate": rate,
                "helpful_rate_interval_95": self._wilson(helpful, observed) if rate is not None else None,
                "critical_regression_count": critical,
                "completion_without_critical_regression_rate": (
                    float(session_count - critical) / session_count if session_count > 0 else None
                ),
                "mean_latency_ms": (
                    sum(s["latency_sum"] / s["outcome_count"] for s in arm_sessions) / session_count
                    if session_count > 0 else 0.0
                ),
                "mean_cost_units": (
                    sum(s["cost_sum"] / s["outcome_count"] for s in arm_sessions) / session_count
                    if session_count > 0 else 0.0
                ),
            }

        critical_regressions = arms["C0"]["critical_regression_count"] + arms["C5"]["critical_regression_count"]
        enough_feedback = (
            arms["C0"]["observed_feedback_count"] >= minimum_observed_per_arm
            and arms["C5"]["observed_feedback_count"] >= minimum_observed_per_arm
        )
        cost_ratio = (
            arms["C5"]["mean_cost_units"] / arms["C0"]["mean_cost_units"]
            if arms["C0"]["mean_cost_units"] > 0.0 else None
        )
        helpfulness_effect = (
            arms["C5"]["helpful_rate"] - arms["C0"]["helpful_rate"]
            if enough_feedback and arms["C5"]["helpful_rate"] is not None and arms["C0"]["helpful_rate"] is not None
            else None
        )
        helpfulness_interval = (
            [
                arms["C5"]["helpful_rate_interval_95"][0] - arms["C0"]["helpful_rate_interval_95"][1],
                arms["C5"]["helpful_rate_interval_95"][1] - arms["C0"]["helpful_rate_interval_95"][0],
            ]
            if enough_feedback and arms["C5"]["helpful_rate_interval_95"] and arms["C0"]["helpful_rate_interval_95"]
            else None
        )
        helpfulness_improved = (helpfulness_interval is not None and helpfulness_interval[0] > 0.0)

        if critical_regressions > 0:
            status = "stopped"
        elif not enough_feedback:
            status = "collecting"
        elif cost_ratio is not None and cost_ratio > 1.25:
            status = "failed_gate"
        elif helpfulness_improved:
            status = "passed"
        else:
            status = "negative_result"

        return {
            "experiment_key": "aporia_real_pilot_v1",
            "tenant_id": tenant_id,
            "analysis_unit": "real_session",
            "feedback_selection": "first_observed_feedback_per_session",
            "evaluability": "observed" if enough_feedback else "not_evaluable",
            "status": status,
            "minimum_observed_sessions_per_arm": minimum_observed_per_arm,
            "arms": arms,
            "helpfulness_absolute_effect": helpfulness_effect,
            "helpfulness_effect_interval_95": helpfulness_interval,
            "cost_overhead_ratio": cost_ratio,
            "gates": {
                "no_critical_regressions": critical_regressions == 0,
                "cost_within_budget": cost_ratio is None or cost_ratio <= 1.25,
                "helpfulness_improved": helpfulness_improved,
            },
            "reason_codes": (
                ["real_user_feedback_observed", "negative_results_retained"]
                if enough_feedback
                else ["insufficient_real_user_feedback"]
            ),
        }

    @staticmethod
    def _wilson(successes: int, total: int) -> list[float]:
        z = 1.959963984540054
        rate = successes / total
        denominator = 1.0 + ((z * z) / total)
        centre = (rate + ((z * z) / (2.0 * total))) / denominator
        margin = (z / denominator) * math.sqrt(((rate * (1.0 - rate)) / total) + ((z * z) / (4.0 * total * total)))
        return [max(0.0, centre - margin), min(1.0, centre + margin)]
