"""Scientific experiments, empirical study, human patterns, real pilot, retention, and advanced runtime ledger for Aporia."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import struct
from typing import Any

from aporia.infrastructure.db import Connection
from aporia.infrastructure.obstruction_engine import AporiaObstructionEngine


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _canonical_json(value: Any) -> str:
    def normalise(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: normalise(v) for k, v in sorted(item.items())}
        if isinstance(item, list):
            return [normalise(x) for x in item]
        return item
    return json.dumps(normalise(value), separators=(",", ":"), ensure_ascii=False)


class AporiaScientificExperiment:
    ARMS = ["C0", "C1", "C2", "C3", "C4", "C5"]
    SEEDS = [104729, 130363, 155921, 196613, 262147]
    HYPOTHESES = [
        "lacuna_path_dependence_exceeds_passive",
        "path_dependence_not_explained_by_noise",
        "negative_autobiography_improves_continuity",
        "identity_survives_model_transplant",
        "localized_reflexive_obstruction_survives_transport_correction",
        "hirt_reduces_incorrect_effects_without_excessive_blocking",
        "factual_consistency_survives_reflexive_nonclosure",
    ]
    METRICS = [
        "path_dependence",
        "identity_continuity",
        "world_obstruction",
        "self_obstruction",
        "incorrect_effect_rate",
        "false_block_rate",
        "factual_consistency",
        "latency_ms",
        "cost_units",
    ]

    def assignment(self, tenant_id: int, episode_ref: str, secret: str) -> dict[str, Any]:
        if tenant_id < 1 or not re.match(r"^[0-9a-f]{64}$", episode_ref) or not secret.strip():
            raise RuntimeError("aporia_experiment_assignment_invalid")
        digest = hmac.new(secret.strip().encode("utf-8"), f"{tenant_id}|{episode_ref}|aporia-c0-c5-v1".encode("utf-8"), hashlib.sha256).hexdigest()
        arm = self.ARMS[int(digest[0:8], 16) % len(self.ARMS)]
        seed = self.SEEDS[int(digest[8:16], 16) % len(self.SEEDS)]
        return {"arm": arm, "seed": seed, "assignment_commitment": digest, "version": 1}

    def evaluate(self, paired_rows: list[dict[str, Any]], metric: str, bootstrap_samples: int = 2000) -> dict[str, Any]:
        if metric not in self.METRICS or bootstrap_samples < 200 or bootstrap_samples > 10000:
            raise RuntimeError("aporia_experiment_metric_invalid")

        differences = []
        for row in paired_rows:
            if not isinstance(row, dict) or not isinstance(row.get("control"), (int, float)) or not isinstance(row.get("treatment"), (int, float)):
                raise RuntimeError("aporia_experiment_pairs_invalid")
            c = float(row["control"])
            t = float(row["treatment"])
            if not math.isfinite(c) or not math.isfinite(t):
                raise RuntimeError("aporia_experiment_pairs_invalid")
            differences.append(t - c)

        if len(differences) < 8:
            return {
                "evaluability": "not_evaluable",
                "reason_codes": ["insufficient_paired_tasks"],
                "sample_size": len(differences),
                "version": 1,
            }

        means = []
        for sample in range(bootstrap_samples):
            s = 0.0
            for index in range(len(differences)):
                digest = hashlib.sha256(f"{metric}|{sample}|{index}".encode("utf-8")).digest()
                pos_val = struct.unpack(">I", digest[:4])[0]
                s += differences[pos_val % len(differences)]
            means.append(s / len(differences))
        means.sort()

        mean = sum(differences) / len(differences)
        control_mean = sum(float(r["control"]) for r in paired_rows) / len(paired_rows)

        low_idx = int(math.floor(0.025 * (bootstrap_samples - 1)))
        high_idx = int(math.floor(0.975 * (bootstrap_samples - 1)))

        return {
            "evaluability": "observed",
            "metric": metric,
            "sample_size": len(differences),
            "absolute_effect": mean,
            "relative_effect": None if abs(control_mean) < 1e-12 else mean / abs(control_mean),
            "confidence_interval_95": [means[low_idx], means[high_idx]],
            "bootstrap_samples": bootstrap_samples,
            "negative_result": mean <= 0.0,
            "reason_codes": ["paired_preregistered_metric", "bootstrap_sampler_v2", "negative_results_retained"],
            "version": 2,
        }

    def preregistration(self) -> dict[str, Any]:
        return {
            "experiment_key": "aporia_c0_c5_v1",
            "arms": self.ARMS,
            "hypotheses": self.HYPOTHESES,
            "metrics": self.METRICS,
            "seeds": self.SEEDS,
            "paired_tasks": True,
            "bootstrap_samples": 2000,
            "bootstrap_sampler": "sha256_with_replacement_v2",
            "posthoc_metric_selection_forbidden": True,
            "version": 2,
        }


class AporiaExperimentCoordinator:
    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_experiment_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def assign(
        self,
        tenant_id: int,
        turn_id: str,
        created_at: str,
        experiment_key: str = "aporia_c0_c5_v1",
        randomization_unit: str | None = None,
    ) -> dict[str, Any]:
        if tenant_id < 1 or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id):
            raise RuntimeError("aporia_experiment_turn_invalid")

        episode_ref = hmac.new(self.secret.encode("utf-8"), f"aporia:{tenant_id}:task:{turn_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        experiment = AporiaScientificExperiment()

        if experiment_key == "aporia_real_pilot_v1":
            protocol = self._real_pilot_protocol()
        elif experiment_key == "aporia_guarded_reversible_v1":
            protocol = self._guarded_protocol()
        else:
            protocol = experiment.preregistration()

        protocol_json = json.dumps(protocol, separators=(",", ":"))
        self._insert_ignore(
            """INSERT INTO aporia_experiment_preregistrations
                (experiment_key, protocol_commitment, arms_json, hypotheses_json, metrics_json, seeds_json, locked_at)
             VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                protocol["experiment_key"],
                hashlib.sha256(protocol_json.encode("utf-8")).hexdigest(),
                self._json(protocol["arms"]),
                self._json(protocol["hypotheses"]),
                self._json(protocol["metrics"]),
                self._json(protocol["seeds"]),
                created_at,
            ],
        )

        if experiment_key == "aporia_real_pilot_v1":
            unit = (randomization_unit or turn_id).strip()
            if not re.match(r"^[A-Za-z0-9_-]{1,191}$", unit):
                raise RuntimeError("aporia_experiment_randomization_unit_invalid")
            digest = hmac.new(self.secret.encode("utf-8"), f"{tenant_id}|{unit}|aporia-real-pilot-v1".encode("utf-8"), hashlib.sha256).hexdigest()
            assignment = {
                "arm": ["C0", "C5"][int(digest[0:8], 16) % 2],
                "seed": AporiaScientificExperiment.SEEDS[int(digest[8:16], 16) % len(AporiaScientificExperiment.SEEDS)],
                "assignment_commitment": digest,
            }
        elif experiment_key == "aporia_guarded_reversible_v1":
            digest = hmac.new(self.secret.encode("utf-8"), f"{tenant_id}|{episode_ref}|aporia-guarded-reversible-v1".encode("utf-8"), hashlib.sha256).hexdigest()
            assignment = {
                "arm": "C5",
                "seed": AporiaScientificExperiment.SEEDS[int(digest[8:16], 16) % len(AporiaScientificExperiment.SEEDS)],
                "assignment_commitment": digest,
            }
        else:
            assignment = experiment.assignment(tenant_id, episode_ref, self.secret)

        assignment_id = self._uuid(hashlib.sha256(f"{tenant_id}|{episode_ref}|{experiment_key}".encode("utf-8")).hexdigest())
        self._insert_ignore(
            """INSERT INTO aporia_experiment_assignments
                (assignment_id, tenant_id, episode_ref, experiment_key, arm, seed, assignment_commitment, assigned_before_outcome, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            [
                assignment_id,
                tenant_id,
                episode_ref,
                experiment_key,
                assignment["arm"],
                assignment["seed"],
                assignment["assignment_commitment"],
                created_at,
                created_at,
            ],
        )

        stmt = self.conn.prepare(
            """SELECT assignment_id, episode_ref, arm, seed, assignment_commitment
             FROM aporia_experiment_assignments
             WHERE tenant_id = ? AND episode_ref = ? AND experiment_key = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, episode_ref, experiment_key])
        row = stmt.fetch()
        if not row:
            raise RuntimeError("aporia_experiment_assignment_unavailable")

        return {
            "assignment_id": str(row["assignment_id"]),
            "episode_ref": str(row["episode_ref"]),
            "arm": str(row["arm"]),
            "seed": int(row["seed"]),
            "assignment_commitment": str(row["assignment_commitment"]),
            "protocol_commitment": hashlib.sha256(protocol_json.encode("utf-8")).hexdigest(),
            "experiment_key": experiment_key,
        }

    def _real_pilot_protocol(self) -> dict[str, Any]:
        return {
            "experiment_key": "aporia_real_pilot_v1",
            "arms": ["C0", "C5"],
            "hypotheses": [
                "c5_improves_explicit_helpfulness_over_c0",
                "c5_preserves_completion_without_critical_regression",
            ],
            "metrics": [
                "explicit_helpfulness",
                "completion_without_critical_regression",
                "latency_ms",
                "cost_units",
            ],
            "seeds": AporiaScientificExperiment.SEEDS,
            "assignment_unit": "real_session",
            "blinded_user_feedback": True,
            "minimum_observed_sessions_per_arm": 8,
            "feedback_selection": "first_observed_feedback_per_session",
            "primary_contrast": "c5_minus_c0_explicit_helpfulness",
            "analysis": "wilson_95_conservative_difference",
            "stop_rules": [
                "critical_regression_observed",
                "privacy_or_security_failure",
                "fallback_rate_above_baseline",
                "token_overhead_above_1_25",
            ],
            "posthoc_metric_selection_forbidden": True,
            "version": 1,
        }

    def _guarded_protocol(self) -> dict[str, Any]:
        return {
            "experiment_key": "aporia_guarded_reversible_v1",
            "arms": ["C5"],
            "hypotheses": ["registered_reversible_internal_effects_remain_within_policy"],
            "metrics": ["critical_regression", "latency_ms", "cost_units", "guarded_usage"],
            "seeds": AporiaScientificExperiment.SEEDS,
            "assignment_unit": "turn",
            "blinded_user_feedback": False,
            "stop_rules": [
                "critical_regression_observed",
                "policy_inactive_or_killed",
                "risk_threshold_exceeded",
                "daily_limit_exceeded",
            ],
            "posthoc_metric_selection_forbidden": True,
            "version": 1,
        }

    def _insert_ignore(self, sql: str, params: list[Any]) -> None:
        clean_sql = re.sub(r"^INSERT INTO ", "INSERT OR IGNORE INTO ", sql)
        stmt = self.conn.prepare(clean_sql)
        stmt.execute(params)

    def _uuid(self, hash_str: str) -> str:
        return (
            hash_str[0:8]
            + "-"
            + hash_str[8:12]
            + "-4"
            + hash_str[13:16]
            + "-a"
            + hash_str[17:20]
            + "-"
            + hash_str[20:32]
        )

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class AporiaGeneralizationHumanPatterns:
    def __init__(self, conn: Connection, environment: str):
        self.conn = conn
        self.environment = environment

    def aggregate(self) -> dict[str, Any]:
        if self.environment.strip().lower() != "staging":
            raise RuntimeError("generalization_r2_staging_required")

        sql = """SELECT wr.score AS relationship_score, wr.stage, wr.created_at,
                    CASE WHEN wr.stage = 'won' THEN COALESCE(wr.won_at, wr.updated_at)
                         ELSE COALESCE(wr.lost_at, wr.updated_at) END AS outcome_at,
                    MAX(CASE WHEN uc.last_participant_message_at IS NOT NULL THEN 1 ELSE 0 END) AS has_response
             FROM workspace_relationships wr
             INNER JOIN workspace_profiles wp ON wp.id = wr.workspace_profile_id
             LEFT JOIN user_conversations uc ON uc.relationship_id = wr.id AND uc.user_id = wp.user_id
             WHERE wr.relationship_type = 'lead' AND wr.stage IN ('won', 'lost')
             GROUP BY wr.id, wr.score, wr.stage, wr.created_at, wr.won_at, wr.lost_at, wr.updated_at"""
        rows = self.conn.query(sql) or []

        cells = [{"sample_count": 0, "positive": 0, "responses": 0, "delay_days": 0.0} for _ in range(4)]
        for row in rows:
            score = max(0, min(100, int(row.get("relationship_score") or 0)))
            quartile = min(3, score // 25)
            cells[quartile]["sample_count"] += 1
            cells[quartile]["positive"] += 1 if str(row.get("stage")) == "won" else 0
            cells[quartile]["responses"] += 1 if int(row.get("has_response") or 0) > 0 else 0
            cells[quartile]["delay_days"] += self._delay_days(str(row["created_at"]), str(row["outcome_at"]))

        source_episodes = len(rows)
        if source_episodes < 100 or any(cell["sample_count"] < 20 for cell in cells):
            raise RuntimeError("generalization_r2_human_sample_insufficient")

        return {
            "schema_version": 2,
            "source_episodes": source_episodes,
            "personal_data_exported": False,
            "quartiles": [
                {
                    "quartile": q,
                    "sample_count": cell["sample_count"],
                    "positive_outcome_rate": cell["positive"] / cell["sample_count"],
                    "response_rate": cell["responses"] / cell["sample_count"],
                    "mean_outcome_delay_days": cell["delay_days"] / cell["sample_count"],
                }
                for q, cell in enumerate(cells)
            ],
        }

    def _delay_days(self, created_at: str, outcome_at: str) -> float:
        try:
            c = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            o = datetime.fromisoformat(outcome_at.replace("Z", "+00:00"))
        except Exception:
            raise RuntimeError("generalization_r2_human_timestamps_invalid")
        return max(0.0, (o.timestamp() - c.timestamp()) / 86400.0)


class AporiaRealPilotEvaluation:
    def __init__(self, conn: Connection):
        self.conn = conn

    def summarize(self, tenant_id: int, minimum_observed_per_arm: int = 8) -> dict[str, Any]:
        if tenant_id < 1 or minimum_observed_per_arm < 8 or minimum_observed_per_arm > 10000:
            raise ValueError("aporia_real_pilot_evaluation_invalid")

        stmt = self.conn.prepare(
            """SELECT a.arm, a.assignment_commitment, o.feedback_correct, o.critical_regression,
                    o.latency_ms, o.cost_units, o.created_at, o.advisory_outcome_id
             FROM aporia_experiment_assignments a
             INNER JOIN aporia_advisory_outcomes o
                ON o.tenant_id = a.tenant_id AND o.assignment_id = a.assignment_id
             WHERE a.tenant_id = ? AND a.experiment_key = 'aporia_real_pilot_v1' AND a.arm IN ('C0','C5')
             ORDER BY a.arm, a.assignment_commitment, o.created_at, o.advisory_outcome_id"""
        )
        stmt.execute([tenant_id])
        rows = stmt.fetch_all() or []

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
            sessions[key]["critical_regression"] = sessions[key]["critical_regression"] or (int(row.get("critical_regression", 0)) == 1)
            sessions[key]["latency_sum"] += float(row.get("latency_ms", 0.0))
            sessions[key]["cost_sum"] += float(row.get("cost_units", 0.0))

        arms = {}
        for arm in ("C0", "C5"):
            arm_sessions = [s for s in sessions.values() if s["arm"] == arm]
            session_count = len(arm_sessions)
            observed = len([s for s in arm_sessions if s["first_feedback"] is not None])
            helpful = len([s for s in arm_sessions if s["first_feedback"] == 1])
            critical = len([s for s in arm_sessions if s["critical_regression"]])
            rate = helpful / observed if observed > 0 else None

            arms[arm] = {
                "session_count": session_count,
                "outcome_count": sum(s["outcome_count"] for s in arm_sessions),
                "observed_feedback_count": observed,
                "helpful_count": helpful,
                "helpful_rate": rate,
                "helpful_rate_interval_95": None if rate is None else self._wilson(helpful, observed),
                "critical_regression_count": critical,
                "completion_without_critical_regression_rate": (session_count - critical) / float(session_count) if session_count > 0 else None,
                "mean_latency_ms": (sum(s["latency_sum"] / s["outcome_count"] for s in arm_sessions) / session_count) if session_count > 0 else 0.0,
                "mean_cost_units": (sum(s["cost_sum"] / s["outcome_count"] for s in arm_sessions) / session_count) if session_count > 0 else 0.0,
            }

        crit_total = arms["C0"]["critical_regression_count"] + arms["C5"]["critical_regression_count"]
        enough_feedback = arms["C0"]["observed_feedback_count"] >= minimum_observed_per_arm and arms["C5"]["observed_feedback_count"] >= minimum_observed_per_arm
        c0_cost = arms["C0"]["mean_cost_units"]
        cost_ratio = (arms["C5"]["mean_cost_units"] / c0_cost) if c0_cost > 0.0 else None

        helpfulness_effect = (arms["C5"]["helpful_rate"] - arms["C0"]["helpful_rate"]) if enough_feedback else None
        helpfulness_interval = None
        if enough_feedback:
            c5_int = arms["C5"]["helpful_rate_interval_95"]
            c0_int = arms["C0"]["helpful_rate_interval_95"]
            helpfulness_interval = [c5_int[0] - c0_int[1], c5_int[1] - c0_int[0]]

        helpfulness_improved = helpfulness_interval is not None and helpfulness_interval[0] > 0.0

        if crit_total > 0:
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
                "no_critical_regressions": crit_total == 0,
                "cost_within_budget": cost_ratio is None or cost_ratio <= 1.25,
                "helpfulness_improved": helpfulness_improved,
            },
            "reason_codes": ["real_user_feedback_observed", "negative_results_retained"] if enough_feedback else ["insufficient_real_user_feedback"],
        }

    def _wilson(self, successes: int, total: int) -> list[float]:
        z = 1.959963984540054
        rate = successes / total
        denominator = 1.0 + ((z * z) / total)
        centre = (rate + ((z * z) / (2.0 * total))) / denominator
        margin = (z / denominator) * math.sqrt(((rate * (1.0 - rate)) / total) + ((z * z) / (4.0 * total * total)))
        return [max(0.0, centre - margin), min(1.0, centre + margin)]


class AporiaRetention:
    TENANT_TABLES = [
        "aporia_product_lacuna_one_shots",
        "aporia_product_runtime_outcomes",
        "aporia_product_context_exposures",
        "aporia_product_state_snapshots",
        "aporia_product_state_items",
        "aporia_effect_outcomes",
        "aporia_effect_contracts",
        "aporia_empirical_corrections",
        "aporia_empirical_evaluations",
        "aporia_empirical_observations",
        "aporia_empirical_runs",
        "aporia_guarded_policy_events",
        "aporia_guarded_usage",
        "aporia_guarded_policies",
        "aporia_real_pilot_feedback_events",
        "aporia_advisory_outcomes",
        "aporia_advisory_exposures",
        "aporia_hirt_decisions",
        "aporia_experiment_outcomes",
        "aporia_obstruction_measurements",
        "aporia_experiment_assignments",
        "aporia_ontology_residuals",
        "aporia_ontology_clusters",
        "aporia_ontology_events",
        "aporia_ontology_primitives",
        "aporia_ontology_versions",
        "aporia_ontology_heads",
        "aporia_identity_commitment_evidence",
        "aporia_identity_commitments",
        "aporia_identity_snapshots",
        "aporia_identity_heads",
        "aporia_negative_autobiography_evaluations",
        "aporia_autobiography_entries",
        "aporia_autobiography_heads",
        "aporia_lacuna_crypto_commits",
        "aporia_lacuna_measurements",
        "aporia_lacuna_branches",
        "aporia_lacuna_runs",
        "aporia_lacuna_outbox",
        "aporia_shadow_envelopes",
        "aporia_perspective_observations",
        "aporia_projection_checkpoints",
        "aporia_event_outbox",
        "aporia_event_parents",
        "aporia_events",
        "aporia_tenant_clocks",
    ]

    def __init__(self, conn: Connection):
        self.conn = conn

    def prune_inactive_tenants(self, retention_days: int, batch_size: int, now: datetime | None = None) -> dict[str, Any]:
        if retention_days < 30 or retention_days > 3650 or batch_size < 1 or batch_size > 1000:
            raise ValueError("aporia_retention_configuration_invalid")

        if not self._table_exists("aporia_tenant_clocks"):
            return self._result()

        curr_now = now or datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff_dt = curr_now - timedelta(days=retention_days)
        cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        candidates = self._inactive_tenant_ids(cutoff, batch_size)
        result = self._result(len(candidates))

        for tenant_id in candidates:
            try:
                deleted = self._purge_tenant(tenant_id, cutoff)
                if deleted is None:
                    result["succeeded"] += 1
                    continue
                result["succeeded"] += 1
                result["deleted_rows"] += deleted
                result["purged_tenants"] += 1
            except Exception:
                if self.conn.in_transaction():
                    self.conn.roll_back()
                result["failed"] += 1
                result["reason_codes"].append("aporia_tenant_retention_failed")

        unique_codes = []
        for c in result["reason_codes"]:
            if c not in unique_codes:
                unique_codes.append(c)
        result["reason_codes"] = unique_codes
        return result

    pruneInactiveTenants = prune_inactive_tenants

    def _inactive_tenant_ids(self, cutoff: str, batch_size: int) -> list[int]:
        stmt = self.conn.prepare("SELECT tenant_id FROM aporia_tenant_clocks WHERE updated_at < ? ORDER BY updated_at, tenant_id LIMIT ?")
        stmt.execute([cutoff, batch_size])
        return [int(r["tenant_id"]) for r in stmt.fetch_all() or []]

    def _purge_tenant(self, tenant_id: int, cutoff: str) -> int | None:
        self.conn.begin_transaction()
        clock = self.conn.prepare("SELECT updated_at FROM aporia_tenant_clocks WHERE tenant_id = ? LIMIT 1")
        clock.execute([tenant_id])
        updated_at = clock.fetch_column()
        if not updated_at or str(updated_at) >= cutoff:
            self.conn.commit()
            return None

        deleted = 0
        for table in self.TENANT_TABLES:
            if not self._table_exists(table) or not self._column_exists(table, "tenant_id"):
                continue
            del_stmt = self.conn.prepare(f"DELETE FROM `{table}` WHERE tenant_id = ?")
            del_stmt.execute([tenant_id])
            deleted += del_stmt.row_count

        self.conn.commit()
        return deleted

    def _result(self, attempted: int = 0) -> dict[str, Any]:
        return {
            "attempted": attempted,
            "succeeded": 0,
            "failed": 0,
            "purged_tenants": 0,
            "deleted_rows": 0,
            "reason_codes": [],
        }

    def _table_exists(self, table: str) -> bool:
        stmt = self.conn.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1")
        stmt.execute([table])
        return stmt.fetch() is not None

    def _column_exists(self, table: str, column: str) -> bool:
        for row in self.conn.query(f"PRAGMA table_info(`{table}`)") or []:
            if row.get("name") == column:
                return True
        return False


class AporiaAdvancedRuntimeLedger:
    SELF_PERSPECTIVES = ["relationship_commitment", "outcome_learning", "identity_continuity"]

    def __init__(self, conn: Connection, secret: str, environment: str = "unknown"):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_advanced_secret_required")
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment

    def record(self, source: dict[str, Any], run_row_id: int, run_id: str) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_advanced_transaction_required")
        tenant_id = int(source.get("tenant_id", 0))
        if tenant_id < 1 or run_row_id < 1 or not re.match(r"^[0-9a-f-]{36}$", run_id):
            raise RuntimeError("aporia_advanced_scope_invalid")

        existing = self.conn.prepare(
            """SELECT classification, world_score, self_score, corrected_self_score, cycle_score
             FROM aporia_obstruction_measurements WHERE tenant_id = ? AND run_id = ? LIMIT 1"""
        )
        existing.execute([tenant_id, run_row_id])
        row = existing.fetch()
        if row:
            res = dict(row)
            res["deduplicated"] = True
            return res

        observations = self._observations(tenant_id, int(source["id"]))
        overlaps = [{"left": i, "right": (i + 1) % len(observations)} for i in range(len(observations))]
        constraints = self._boolean_constraints(tenant_id, int(source["id"]))

        self_ref = str(source.get("event_kind")) in ("model.started", "model.completed", "turn.completed")
        engine = AporiaObstructionEngine()
        measurement = engine.evaluate(
            observations,
            overlaps,
            constraints,
            self_ref,
            AporiaScientificExperiment.SEEDS,
        )

        meas_hash = hashlib.sha256(f"{tenant_id}|{run_id}|obstruction-v1".encode("utf-8")).hexdigest()
        measurement_id = self._uuid(meas_hash)
        op_id = hashlib.sha256(f"{tenant_id}|{run_id}|measure-obstruction".encode("utf-8")).hexdigest()

        reason_code = str(measurement["reason_codes"][-1] if measurement["reason_codes"] else "measurement_recorded")

        stmt = self.conn.prepare(
            """INSERT INTO aporia_obstruction_measurements
                (measurement_id, tenant_id, run_id, world_score, self_score, corrected_self_score, cycle_score,
                 classification, satisfiable, self_referential, unsat_constraint_hashes_json, multi_start_scores_json,
                 reason_codes_json, event_name, event_version, environment, stream, category, component, operation_id,
                 actor_type, action_name, lifecycle_phase, outcome, reason_code, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     'aporia.obstruction.measurement.recorded', 1, ?, 'system', 'audit', 'aporia-obstruction-engine', ?,
                     'worker', 'measure_obstruction', 'succeeded', 'succeeded', ?, ?, ?)"""
        )
        stmt.execute([
            measurement_id,
            tenant_id,
            run_row_id,
            measurement["world_score"],
            measurement["self_score"],
            measurement["corrected_self_score"],
            measurement["cycle_score"],
            measurement["classification"],
            1 if measurement["satisfiable"] else 0,
            1 if measurement["self_referential"] else 0,
            json.dumps(measurement["unsat_constraint_hashes"], separators=(",", ":")),
            json.dumps(measurement["multi_start_scores"], separators=(",", ":")),
            json.dumps(measurement["reason_codes"], separators=(",", ":")),
            self.environment[:24],
            op_id,
            reason_code[:80],
            source["ingested_at"],
            source["ingested_at"],
        ])

        self._record_experiment_outcomes(source, run_row_id, run_id, measurement)
        res = dict(measurement)
        res["measurement_id"] = measurement_id
        res["deduplicated"] = False
        return res

    def _observations(self, tenant_id: int, event_row_id: int) -> list[dict[str, Any]]:
        stmt = self.conn.prepare(
            """SELECT perspective, epistemic_status, uncertainty_codes_json, value_json
             FROM aporia_perspective_observations
             WHERE tenant_id = ? AND event_id = ? AND projection_version = 2 ORDER BY perspective"""
        )
        stmt.execute([tenant_id, event_row_id])
        rows = stmt.fetch_all() or []
        if len(rows) != 6:
            raise RuntimeError("aporia_advanced_observations_unavailable")

        result = []
        for row in rows:
            uncertainty = json.loads(str(row["uncertainty_codes_json"]))
            value = json.loads(str(row["value_json"]))
            numbers = self._numbers(value)
            numeric_mag = 0.0 if not numbers else min(1.0, math.log(1.0 + sum(abs(n) for n in numbers)) / 10.0)
            u_count = len(uncertainty) if isinstance(uncertainty, list) else 0
            ep_status = str(row["epistemic_status"])

            result.append({
                "scope": "self" if str(row["perspective"]) in self.SELF_PERSPECTIVES else "world",
                "vector": [
                    1.0 if ep_status == "observed" else 0.0,
                    1.0 / (1.0 + u_count),
                    numeric_mag,
                ],
                "weight": 1.0 if ep_status == "observed" else 0.5,
                "transport_correctable": True,
            })
        return result

    def _boolean_constraints(self, tenant_id: int, event_row_id: int) -> list[dict[str, Any]]:
        stmt = self.conn.prepare(
            """SELECT perspective, value_json FROM aporia_perspective_observations
             WHERE tenant_id = ? AND event_id = ? AND projection_version = 2 ORDER BY perspective"""
        )
        stmt.execute([tenant_id, event_row_id])
        rows = stmt.fetch_all() or []
        constraints = []
        for row in rows:
            value = json.loads(str(row["value_json"]))
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(item, bool) or not re.match(r"^[a-z][a-z0-9_]{0,63}$", str(key)):
                        continue
                    comm = hashlib.sha256(f"{row['perspective']}|{key}|{'1' if item else '0'}".encode("utf-8")).hexdigest()
                    constraints.append({
                        "variable": str(key),
                        "value": item,
                        "commitment": comm,
                    })
        return constraints

    def _record_experiment_outcomes(self, source: dict[str, Any], run_row_id: int, run_id: str, measurement: dict[str, Any]) -> None:
        tenant_id = int(source["tenant_id"])
        episode_ref = str(source.get("task_ref", "")).strip()
        if not re.match(r"^[0-9a-f]{64}$", episode_ref):
            return

        assignment = self.conn.prepare(
            "SELECT assignment_id FROM aporia_experiment_assignments WHERE tenant_id = ? AND episode_ref = ? AND experiment_key = 'aporia_c0_c5_v1' LIMIT 1"
        )
        assignment.execute([tenant_id, episode_ref])
        assignment_id = assignment.fetch_column()
        if not assignment_id:
            return

        path = self.conn.prepare(
            "SELECT numeric_value FROM aporia_lacuna_measurements WHERE tenant_id = ? AND run_id = ? AND metric_name = 'order_sensitivity' LIMIT 1"
        )
        path.execute([tenant_id, run_row_id])
        path_value = path.fetch_column()

        metrics = {
            "world_obstruction": (float(measurement["world_score"]), "lower"),
            "self_obstruction": (float(measurement["corrected_self_score"]), "lower"),
        }
        if path_value is not None and path_value is not False:
            metrics["path_dependence"] = (float(path_value), "lower")

        for metric, (val, direction) in metrics.items():
            outcome_hash = hashlib.sha256(f"{tenant_id}|{run_id}|{metric}".encode("utf-8")).hexdigest()
            outcome_id = self._uuid(outcome_hash)
            stmt = self.conn.prepare(
                """INSERT OR IGNORE INTO aporia_experiment_outcomes
                    (outcome_id, tenant_id, assignment_id, run_id, metric_name, numeric_value, favorable_direction, reason_codes_json)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
            )
            stmt.execute([
                outcome_id,
                tenant_id,
                str(assignment_id),
                run_row_id,
                metric,
                val,
                direction,
                json.dumps(["assignment_preceded_outcome", "preregistered_metric"], separators=(",", ":")),
            ])

    def _numbers(self, value: Any) -> list[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            fv = float(value)
            return [fv if math.isfinite(fv) else 0.0]
        if isinstance(value, list):
            res = []
            for item in value:
                res.extend(self._numbers(item))
            return res[:32]
        if isinstance(value, dict):
            res = []
            for item in value.values():
                res.extend(self._numbers(item))
            return res[:32]
        return []

    def _uuid(self, hash_str: str) -> str:
        return (
            hash_str[0:8]
            + "-"
            + hash_str[8:12]
            + "-4"
            + hash_str[13:16]
            + "-a"
            + hash_str[17:20]
            + "-"
            + hash_str[20:32]
        )


class AporiaEmpiricalStudy:
    ARMS = ["C0", "C1", "C2", "C3", "C4", "C5"]
    MODELS = ["gpt-5.6-luna", "gpt-5.6-terra"]
    KINDS = ["path", "negative_autobiography", "hirt", "factual_consistency", "obstruction"]
    SYNTHETIC_TENANT_MIN = 9000000000000000000

    def __init__(self, conn: Connection, protocol_path: str, environment: str):
        self.conn = conn
        self.protocol_path = protocol_path
        self.environment = environment

    def prepare(self, tenant_id: int, operator_user_id: int) -> dict[str, Any]:
        if tenant_id < self.SYNTHETIC_TENANT_MIN or operator_user_id < 1:
            raise RuntimeError("aporia_empirical_scope_invalid")

        protocol = self._protocol()
        commitment = hashlib.sha256(_canonical_json(protocol).encode("utf-8")).hexdigest()
        run_id = self._random_uuid()
        op_id = hashlib.sha256(f"{tenant_id}|{run_id}|{protocol['protocol_key']}".encode("utf-8")).hexdigest()
        now = _now_str()

        stmt = self.conn.prepare(
            """INSERT INTO aporia_empirical_runs
                (run_id, tenant_id, protocol_key, protocol_commitment, status, operator_user_id,
                 task_count, arm_count, model_count, event_name, event_version, environment, stream,
                 category, component, operation_id, actor_type, action_name, lifecycle_phase, outcome,
                 reason_code, started_at)
             VALUES (?, ?, ?, ?, 'prepared', ?, ?, 6, 2, 'aporia.experiment.empirical.run', 1, ?,
                     'system', 'audit', 'aporia-empirical-study', ?, 'admin',
                     'execute_empirical_experiment', 'accepted', 'accepted', 'protocol_locked', ?)"""
        )
        stmt.execute([
            run_id,
            tenant_id,
            str(protocol["protocol_key"]),
            commitment,
            operator_user_id,
            len(protocol["tasks"]),
            self.environment[:24],
            op_id,
            now,
        ])

        return {
            "run_id": run_id,
            "protocol_key": str(protocol["protocol_key"]),
            "protocol_commitment": commitment,
            "task_count": len(protocol["tasks"]),
            "observation_count": len(protocol["tasks"]) * len(self.ARMS) * len(self.MODELS),
        }

    def ingest(self, tenant_id: int, operator_user_id: int, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if tenant_id < self.SYNTHETIC_TENANT_MIN or operator_user_id < 1 or not re.match(r"^[0-9a-f-]{36}$", run_id):
            raise RuntimeError("aporia_empirical_scope_invalid")

        protocol = self._protocol()
        protocol_commitment = hashlib.sha256(_canonical_json(protocol).encode("utf-8")).hexdigest()

        if (
            result.get("protocol_key") != protocol["protocol_key"]
            or result.get("protocol_commitment") != protocol_commitment
            or not isinstance(result.get("observations"), list)
        ):
            raise RuntimeError("aporia_empirical_result_invalid")

        expected_count = len(protocol["tasks"]) * len(self.ARMS) * len(self.MODELS)
        if len(result["observations"]) != expected_count:
            raise RuntimeError("aporia_empirical_result_incomplete")

        result_commitment = hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest()
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            run = self._lock_run(tenant_id, run_id)
            if (
                not hmac.compare_digest(str(run["protocol_commitment"]), protocol_commitment)
                or int(run["operator_user_id"]) != operator_user_id
            ):
                raise RuntimeError("aporia_empirical_run_forbidden")

            if str(run["status"]) == "completed":
                if not hmac.compare_digest(str(run.get("result_commitment", "")), result_commitment):
                    raise RuntimeError("aporia_empirical_result_conflict")
                if started:
                    self.conn.commit()
                return self.summary(tenant_id, run_id)

            if str(run["status"]) != "prepared":
                raise RuntimeError("aporia_empirical_run_unavailable")

            tasks = {str(task["id"]): task for task in protocol["tasks"]}
            cells = {}
            for obs in result["observations"]:
                validated = self._validate_observation(obs, tasks)
                cell_key = f"{validated['task_id']}|{validated['arm']}|{validated['model']}"
                if cell_key in cells:
                    raise RuntimeError("aporia_empirical_result_duplicate")
                cells[cell_key] = validated
                self._insert_observation(tenant_id, run_id, validated, tasks[validated["task_id"]])

            if len(cells) != expected_count:
                raise RuntimeError("aporia_empirical_result_incomplete")

            evals = self._evaluations(tasks, cells, str(protocol["protocol_key"]))
            for ev in evals:
                self._insert_evaluation(tenant_id, run_id, ev)

            now = _now_str()
            complete = self.conn.prepare(
                """UPDATE aporia_empirical_runs
                 SET status = 'completed', result_commitment = ?, observation_count = ?,
                     lifecycle_phase = 'succeeded', outcome = 'succeeded', reason_code = ?,
                     completed_at = ?, updated_at = ?
                 WHERE tenant_id = ? AND run_id = ? AND status = 'prepared'"""
            )
            complete.execute([
                result_commitment,
                expected_count,
                "hypotheses_evaluated_bootstrap_" + str(protocol["protocol_key"])[-2:],
                now,
                now,
                tenant_id,
                run_id,
            ])
            if complete.row_count != 1:
                raise RuntimeError("aporia_empirical_run_conflict")

            if started:
                self.conn.commit()
            return self.summary(tenant_id, run_id)
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def summary(self, tenant_id: int, run_id: str) -> dict[str, Any]:
        stmt = self.conn.prepare(
            """SELECT status, protocol_key, protocol_commitment, result_commitment, task_count, observation_count
             FROM aporia_empirical_runs WHERE tenant_id = ? AND run_id = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, run_id])
        run = stmt.fetch()
        if not run:
            raise RuntimeError("aporia_empirical_run_unavailable")

        ev_stmt = self.conn.prepare(
            """SELECT hypothesis_key, metric_name, control_arm, treatment_arm, sample_size,
                    control_mean, treatment_mean, absolute_effect, relative_effect,
                    confidence_low, confidence_high, verdict, negative_result, reason_codes_json
             FROM aporia_empirical_evaluations WHERE tenant_id = ? AND run_id = ? ORDER BY id"""
        )
        ev_stmt.execute([tenant_id, run_id])
        evaluations = []
        for row in ev_stmt.fetch_all() or []:
            evaluations.append({
                "hypothesis": str(row["hypothesis_key"]),
                "metric": str(row["metric_name"]),
                "control_arm": str(row["control_arm"]),
                "treatment_arm": str(row["treatment_arm"]),
                "sample_size": int(row["sample_size"]),
                "control_mean": float(row["control_mean"]),
                "treatment_mean": float(row["treatment_mean"]),
                "absolute_effect": float(row["absolute_effect"]),
                "relative_effect": None if row["relative_effect"] is None else float(row["relative_effect"]),
                "confidence_interval_95": [float(row["confidence_low"]), float(row["confidence_high"])],
                "verdict": str(row["verdict"]),
                "negative_result": bool(row["negative_result"]),
                "reason_codes": json.loads(str(row["reason_codes_json"])),
            })

        protocol = self._protocol()
        if not hmac.compare_digest(str(run["protocol_key"]), str(protocol["protocol_key"])):
            raise RuntimeError("aporia_empirical_protocol_mismatch")

        final_treatment = protocol["protocol_key"] in ("aporia_c0_c5_v4", "aporia_c0_c5_v5", "aporia_c0_c5_v6")
        treatment_cond = "arm = 'C5'" if final_treatment else "arm IN ('C4','C5')"
        token_col = "input_tokens" if protocol.get("token_overhead_metric") == "input_tokens" else "total_tokens"

        gate_stmt = self.conn.prepare(
            f"""SELECT
                SUM(CASE WHEN {treatment_cond} THEN critical_regression ELSE 0 END) AS critical_regressions,
                AVG(CASE WHEN arm = 'C0' THEN latency_ms END) AS baseline_latency,
                AVG(CASE WHEN {treatment_cond} THEN latency_ms END) AS treatment_latency,
                AVG(CASE WHEN arm = 'C0' THEN {token_col} END) AS baseline_tokens,
                AVG(CASE WHEN {treatment_cond} THEN {token_col} END) AS treatment_tokens,
                AVG(CASE WHEN arm = 'C0' THEN total_tokens END) AS baseline_total_tokens,
                AVG(CASE WHEN {treatment_cond} THEN total_tokens END) AS treatment_total_tokens,
                AVG(CASE WHEN arm = 'C0' THEN correct END) AS baseline_accuracy,
                AVG(CASE WHEN arm = 'C5' THEN correct END) AS treatment_accuracy,
                MIN(CASE WHEN task_kind = 'factual_consistency' AND {treatment_cond} THEN correct ELSE 1 END) AS advisory_respected_evidence
             FROM aporia_empirical_observations WHERE tenant_id = ? AND run_id = ?"""
        )
        gate_stmt.execute([tenant_id, run_id])
        gates = gate_stmt.fetch() or {}

        base_lat = float(gates.get("baseline_latency") or 0.0)
        treat_lat = float(gates.get("treatment_latency") or 0.0)
        latency_overhead = max(0.0, treat_lat - base_lat)

        base_tok = float(gates.get("baseline_tokens") or 0.0)
        treat_tok = float(gates.get("treatment_tokens") or 0.0)
        token_ratio = (treat_tok / base_tok) if base_tok > 0.0 else None

        base_tot_tok = float(gates.get("baseline_total_tokens") or 0.0)
        treat_tot_tok = float(gates.get("treatment_total_tokens") or 0.0)
        total_token_ratio = (treat_tot_tok / base_tot_tok) if base_tot_tok > 0.0 else None

        latency_budget = float(protocol.get("latency_overhead_budget_ms", 0.0))
        token_budget = float(protocol.get("token_overhead_ratio", 0.0))

        functional_eval = None
        for ev in evaluations:
            if ev["hypothesis"] == "functional_improvement_over_baseline":
                functional_eval = ev
                break

        corr_stmt = self.conn.prepare(
            """SELECT correction_id, reason_code, previous_evaluations_commitment,
                    corrected_evaluations_commitment, created_at
             FROM aporia_empirical_corrections WHERE tenant_id = ? AND run_id = ? ORDER BY id"""
        )
        corr_stmt.execute([tenant_id, run_id])
        corrections = [
            {
                "correction_id": str(r["correction_id"]),
                "reason_code": str(r["reason_code"]),
                "previous_evaluations_commitment": str(r["previous_evaluations_commitment"]),
                "corrected_evaluations_commitment": str(r["corrected_evaluations_commitment"]),
                "created_at": str(r["created_at"]),
            }
            for r in corr_stmt.fetch_all() or []
        ]

        return {
            "run_id": run_id,
            "status": str(run["status"]),
            "protocol_key": str(run["protocol_key"]),
            "protocol_commitment": str(run["protocol_commitment"]),
            "result_commitment": run["result_commitment"],
            "task_count": int(run["task_count"]),
            "observation_count": int(run["observation_count"]),
            "evaluations": evaluations,
            "corrections": corrections,
            "advisory_gates": {
                "no_critical_regressions": int(gates.get("critical_regressions") or 0) == 0,
                "latency_overhead_ms": latency_overhead,
                "latency_within_budget": latency_overhead <= latency_budget,
                "token_overhead_ratio": token_ratio,
                "token_overhead_metric": token_col,
                "total_token_overhead_ratio": total_token_ratio,
                "tokens_within_budget": token_ratio is not None and token_ratio <= token_budget,
                "baseline_accuracy": float(gates.get("baseline_accuracy") or 0.0),
                "treatment_accuracy": float(gates.get("treatment_accuracy") or 0.0),
                "functional_improvement_over_baseline": (
                    functional_eval["verdict"] == "supported" and functional_eval["treatment_mean"] > functional_eval["control_mean"]
                ) if functional_eval else None,
                "advisory_did_not_dominate_confirmed_evidence": int(gates.get("advisory_respected_evidence") or 0) == 1,
            },
        }

    def correct_bootstrap(self, tenant_id: int, operator_user_id: int, run_id: str) -> dict[str, Any]:
        if tenant_id < self.SYNTHETIC_TENANT_MIN or operator_user_id < 1 or not re.match(r"^[0-9a-f-]{36}$", run_id):
            raise RuntimeError("aporia_empirical_scope_invalid")

        started = False
        if hasattr(self.conn, "in_transaction") and not self.conn.in_transaction():
            self.conn.begin_transaction()
            started = True

        try:
            run = self._lock_run(tenant_id, run_id)
            if int(run["operator_user_id"]) != operator_user_id or str(run["status"]) != "completed":
                raise RuntimeError("aporia_empirical_run_forbidden")

            existing = self.conn.prepare(
                """SELECT correction_id FROM aporia_empirical_corrections
                 WHERE tenant_id = ? AND run_id = ? AND reason_code = 'bootstrap_sampler_invalid_v1' LIMIT 1"""
            )
            existing.execute([tenant_id, run_id])
            val = existing.fetch_column()
            if val is not None and val is not False:
                if started:
                    self.conn.commit()
                return self.summary(tenant_id, run_id)

            if str(run.get("reason_code", "")) != "hypotheses_evaluated":
                raise RuntimeError("aporia_empirical_correction_not_applicable")

            protocol = self._protocol()
            if not hmac.compare_digest(str(run["protocol_key"]), str(protocol["protocol_key"])):
                raise RuntimeError("aporia_empirical_protocol_mismatch")

            tasks = {}
            task_refs = {}
            for task in protocol["tasks"]:
                t_id = str(task["id"])
                tasks[t_id] = task
                task_refs[hashlib.sha256(t_id.encode("utf-8")).hexdigest()] = t_id

            obs_stmt = self.conn.prepare(
                """SELECT task_ref, arm, model_name, decision_label, correct
                 FROM aporia_empirical_observations WHERE tenant_id = ? AND run_id = ?"""
            )
            obs_stmt.execute([tenant_id, run_id])
            cells = {}
            for row in obs_stmt.fetch_all() or []:
                t_id = task_refs.get(str(row["task_ref"]))
                if not t_id:
                    raise RuntimeError("aporia_empirical_correction_observation_invalid")
                cells[f"{t_id}|{row['arm']}|{row['model_name']}"] = {
                    "decision": str(row["decision_label"]),
                    "correct": bool(row["correct"]),
                }

            expected_count = len(tasks) * len(self.ARMS) * len(self.MODELS)
            if len(cells) != expected_count:
                raise RuntimeError("aporia_empirical_correction_observation_invalid")

            previous = self.summary(tenant_id, run_id)["evaluations"]
            corrected = self._evaluations(tasks, cells, str(run["protocol_key"]))
            previous_json = _canonical_json(previous)

            for evaluation in corrected:
                reasons = list(evaluation["reason_codes"])
                if "bootstrap_sampler_invalidated_v1" not in reasons:
                    reasons.append("bootstrap_sampler_invalidated_v1")
                evaluation["reason_codes"] = reasons

                update = self.conn.prepare(
                    """UPDATE aporia_empirical_evaluations
                     SET sample_size = ?, control_mean = ?, treatment_mean = ?, absolute_effect = ?,
                         relative_effect = ?, confidence_low = ?, confidence_high = ?, verdict = ?,
                         negative_result = ?, reason_codes_json = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE tenant_id = ? AND run_id = ? AND hypothesis_key = ?"""
                )
                update.execute([
                    evaluation["sample_size"],
                    evaluation["control_mean"],
                    evaluation["treatment_mean"],
                    evaluation["absolute_effect"],
                    evaluation["relative_effect"],
                    evaluation["confidence_low"],
                    evaluation["confidence_high"],
                    evaluation["verdict"],
                    1 if evaluation["negative_result"] else 0,
                    json.dumps(evaluation["reason_codes"]),
                    tenant_id,
                    run_id,
                    evaluation["hypothesis"],
                ])
                if update.row_count != 1:
                    raise RuntimeError("aporia_empirical_correction_conflict")

            corrected_json = _canonical_json(self.summary(tenant_id, run_id)["evaluations"])
            operation_id = hashlib.sha256(f"{tenant_id}|{run_id}|bootstrap_sampler_invalid_v1".encode("utf-8")).hexdigest()
            correction_id = self._uuid(hashlib.sha256(f"{operation_id}|correction".encode("utf-8")).hexdigest())

            insert = self.conn.prepare(
                """INSERT INTO aporia_empirical_corrections
                    (correction_id, tenant_id, run_id, operator_user_id, reason_code,
                     previous_evaluations_json, corrected_evaluations_json,
                     previous_evaluations_commitment, corrected_evaluations_commitment,
                     event_name, event_version, environment, stream, category, component,
                     operation_id, actor_type, action_name, lifecycle_phase, outcome)
                 VALUES (?, ?, ?, ?, 'bootstrap_sampler_invalid_v1', ?, ?, ?, ?,
                         'aporia.experiment.empirical.corrected', 1, ?, 'system', 'audit',
                         'aporia-empirical-study', ?, 'admin', 'correct_empirical_evaluation',
                         'succeeded', 'succeeded')"""
            )
            insert.execute([
                correction_id,
                tenant_id,
                run_id,
                operator_user_id,
                previous_json,
                corrected_json,
                hashlib.sha256(previous_json.encode("utf-8")).hexdigest(),
                hashlib.sha256(corrected_json.encode("utf-8")).hexdigest(),
                self.environment[:24],
                operation_id,
            ])

            run_update = self.conn.prepare(
                """UPDATE aporia_empirical_runs SET reason_code = 'hypotheses_evaluated_bootstrap_v2',
                 updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND run_id = ?"""
            )
            run_update.execute([tenant_id, run_id])
            if run_update.row_count != 1:
                raise RuntimeError("aporia_empirical_correction_conflict")

            if started:
                self.conn.commit()

            return self.summary(tenant_id, run_id)
        except Exception as exc:
            if started and hasattr(self.conn, "in_transaction") and self.conn.in_transaction():
                self.conn.roll_back()
            raise exc

    correctBootstrap = correct_bootstrap

    def _protocol(self) -> dict[str, Any]:
        if not os.path.isfile(self.protocol_path) or os.path.getsize(self.protocol_path) > 131072:
            raise RuntimeError("aporia_empirical_protocol_unavailable")

        with open(self.protocol_path, "r", encoding="utf-8") as f:
            protocol = json.load(f)

        if isinstance(protocol, dict) and "base_protocol" in protocol:
            base_name = str(protocol["base_protocol"])
            if os.path.basename(base_name) != base_name:
                raise RuntimeError("aporia_empirical_protocol_invalid")
            base_path = os.path.join(os.path.dirname(self.protocol_path), base_name)
            if not os.path.isfile(base_path) or os.path.getsize(base_path) > 131072:
                raise RuntimeError("aporia_empirical_protocol_invalid")
            with open(base_path, "rb") as bf:
                base_bytes = bf.read()
            if not hmac.compare_digest(str(protocol.get("base_protocol_commitment", "")), hashlib.sha256(base_bytes).hexdigest()):
                raise RuntimeError("aporia_empirical_protocol_invalid")

            base = AporiaEmpiricalStudy(self.conn, base_path, self.environment)._protocol()
            overrides = protocol.get("task_overrides")
            if not isinstance(base, dict) or not isinstance(base.get("tasks"), list) or not isinstance(overrides, dict):
                raise RuntimeError("aporia_empirical_protocol_invalid")

            tasks = []
            task_ids = []
            for t in base["tasks"]:
                t_id = str(t.get("id", ""))
                ov = overrides.get(t_id, {})
                if not isinstance(ov, dict) or any(k not in ("prompt", "memory", "passive", "lacuna", "hirt") for k in ov.keys()):
                    raise RuntimeError("aporia_empirical_protocol_invalid")
                merged_t = dict(t)
                merged_t.update(ov)
                tasks.append(merged_t)
                task_ids.append(t_id)

            override_ids = sorted(list(overrides.keys()))
            if overrides and sorted(task_ids) != override_ids:
                raise RuntimeError("aporia_empirical_protocol_invalid")

            protocol["tasks"] = tasks

        if (
            not isinstance(protocol, dict)
            or protocol.get("protocol_key") not in ("aporia_c0_c5_v2", "aporia_c0_c5_v3", "aporia_c0_c5_v4", "aporia_c0_c5_v5", "aporia_c0_c5_v6")
            or protocol.get("arms") != self.ARMS
            or protocol.get("models") != self.MODELS
            or not isinstance(protocol.get("tasks"), list)
            or len(protocol["tasks"]) < 8
        ):
            raise RuntimeError("aporia_empirical_protocol_invalid")

        seen_ids = set()
        for t in protocol["tasks"]:
            if (
                not isinstance(t, dict)
                or not re.match(r"^[a-z0-9_]{1,40}$", str(t.get("id", "")))
                or t.get("kind") not in self.KINDS
                or t.get("expected") not in ("A", "B")
                or not isinstance(t.get("seed"), int)
                or not isinstance(t.get("critical"), bool)
                or not isinstance(t.get("prompt"), str)
                or not isinstance(t.get("memory"), list)
                or not isinstance(t.get("passive"), list)
                or not isinstance(t.get("lacuna"), list)
                or not isinstance(t.get("hirt"), list)
                or t["id"] in seen_ids
            ):
                raise RuntimeError("aporia_empirical_protocol_invalid")
            seen_ids.add(t["id"])

        return protocol

    def _validate_observation(self, row: dict[str, Any], tasks: dict[str, Any]) -> dict[str, Any]:
        task_id = str(row.get("task_id", ""))
        task = tasks.get(task_id)
        arm = str(row.get("arm", ""))
        model = str(row.get("model", ""))
        decision = str(row.get("decision", ""))
        confidence = row.get("confidence")

        if (
            not task
            or arm not in self.ARMS
            or model not in self.MODELS
            or decision not in ("A", "B")
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or float(confidence) < 0.0
            or float(confidence) > 1.0
            or int(row.get("seed", -1)) != int(task["seed"])
            or not hmac.compare_digest(hashlib.sha256(_canonical_json(task).encode("utf-8")).hexdigest(), str(row.get("task_commitment", "")))
            or not re.match(r"^[0-9a-f]{64}$", str(row.get("decision_commitment", "")))
        ):
            raise RuntimeError("aporia_empirical_observation_invalid")

        for fld in ("input_tokens", "output_tokens", "total_tokens", "latency_ms"):
            v = row.get(fld)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise RuntimeError("aporia_empirical_observation_invalid")

        if row["total_tokens"] != row["input_tokens"] + row["output_tokens"]:
            raise RuntimeError("aporia_empirical_observation_invalid")

        correct = decision == task["expected"]
        if row.get("correct") != correct or row.get("critical_regression") != (task["critical"] and not correct):
            raise RuntimeError("aporia_empirical_observation_label_conflict")

        return row

    def _insert_observation(self, tenant_id: int, run_id: str, row: dict[str, Any], task: dict[str, Any]) -> None:
        task_ref = hashlib.sha256(str(row["task_id"]).encode("utf-8")).hexdigest()
        pair_ref = hashlib.sha256(f"{run_id}|{row['task_id']}|{row['model']}".encode("utf-8")).hexdigest()
        operation = hashlib.sha256(f"{tenant_id}|{run_id}|{row['task_id']}|{row['arm']}|{row['model']}".encode("utf-8")).hexdigest()
        obs_id = self._uuid(hashlib.sha256(f"{operation}|observation".encode("utf-8")).hexdigest())

        correct = bool(row["correct"])
        confidence = float(row["confidence"])
        brier = (confidence - (1.0 if correct else 0.0)) ** 2
        calibration = abs(confidence - (1.0 if correct else 0.0))

        stmt = self.conn.prepare(
            """INSERT INTO aporia_empirical_observations
                (observation_id, tenant_id, run_id, task_ref, task_commitment, pair_ref, task_kind,
                 arm, model_name, seed, decision_label, decision_commitment, expected_label, correct,
                 confidence, brier_score, calibration_error, critical_regression, input_tokens,
                 output_tokens, total_tokens, latency_ms)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            obs_id,
            tenant_id,
            run_id,
            task_ref,
            row["task_commitment"],
            pair_ref,
            task["kind"],
            row["arm"],
            row["model"],
            row["seed"],
            row["decision"],
            row["decision_commitment"],
            task["expected"],
            1 if correct else 0,
            confidence,
            brier,
            calibration,
            1 if row["critical_regression"] else 0,
            row["input_tokens"],
            row["output_tokens"],
            row["total_tokens"],
            row["latency_ms"],
        ])

    def _evaluations(self, tasks: dict[str, Any], cells: dict[str, Any], protocol_key: str) -> list[dict[str, Any]]:
        specifications = [
            ("lacuna_path_dependence_exceeds_passive", "path_dependence", "path", "C2", "C4"),
            ("path_dependence_not_explained_by_noise", "path_dependence", "path", "C3", "C4"),
            ("negative_autobiography_improves_continuity", "path_dependence", "negative_autobiography", "C1", "C4"),
            ("localized_reflexive_obstruction_survives_transport_correction", "obstruction_localization", "obstruction", "C2", "C4"),
            ("hirt_reduces_incorrect_effects_without_excessive_blocking", "hirt_utility", "hirt", "C4", "C5"),
            ("factual_consistency_survives_reflexive_nonclosure", "factual_consistency", "factual_consistency", "C0", "C4"),
        ]

        evaluations = []
        for hypothesis, metric, kind, control, treatment in specifications:
            pairs = []
            for task_id, task in tasks.items():
                if task["kind"] != kind:
                    continue
                for model in self.MODELS:
                    c_cell = cells[f"{task_id}|{control}|{model}"]
                    t_cell = cells[f"{task_id}|{treatment}|{model}"]
                    pairs.append({
                        "control": 1.0 if c_cell["correct"] else 0.0,
                        "treatment": 1.0 if t_cell["correct"] else 0.0,
                    })
            evaluations.append(self._evaluate(hypothesis, metric, control, treatment, pairs))

        identity_pairs = []
        for task_id in tasks.keys():
            cLuna = cells[f"{task_id}|C1|gpt-5.6-luna"]
            cTerra = cells[f"{task_id}|C1|gpt-5.6-terra"]
            tLuna = cells[f"{task_id}|C4|gpt-5.6-luna"]
            tTerra = cells[f"{task_id}|C4|gpt-5.6-terra"]
            identity_pairs.append({
                "control": 1.0 if (cLuna["correct"] and cTerra["correct"] and cLuna["decision"] == cTerra["decision"]) else 0.0,
                "treatment": 1.0 if (tLuna["correct"] and tTerra["correct"] and tLuna["decision"] == tTerra["decision"]) else 0.0,
            })

        evaluations.insert(3, self._evaluate(
            "identity_survives_model_transplant",
            "identity_continuity",
            "C1",
            "C4",
            identity_pairs,
        ))

        if protocol_key in ("aporia_c0_c5_v3", "aporia_c0_c5_v4", "aporia_c0_c5_v5", "aporia_c0_c5_v6"):
            pairs = []
            for task_id in tasks.keys():
                for model in self.MODELS:
                    c0_cell = cells[f"{task_id}|C0|{model}"]
                    c5_cell = cells[f"{task_id}|C5|{model}"]
                    pairs.append({
                        "control": 1.0 if c0_cell["correct"] else 0.0,
                        "treatment": 1.0 if c5_cell["correct"] else 0.0,
                    })
            evaluations.append(self._evaluate(
                "functional_improvement_over_baseline",
                "decision_accuracy",
                "C0",
                "C5",
                pairs,
            ))

        return evaluations

    def _evaluate(self, hypothesis: str, metric: str, control_arm: str, treatment_arm: str, pairs: list[dict[str, float]]) -> dict[str, Any]:
        if len(pairs) < 8:
            raise RuntimeError("aporia_empirical_pairs_insufficient")

        differences = [p["treatment"] - p["control"] for p in pairs]
        bootstrap = []
        n_pairs = len(pairs)

        for sample in range(5000):
            s = 0.0
            for index in range(n_pairs):
                digest = hashlib.sha256(f"{hypothesis}|{sample}|{index}".encode("utf-8")).digest()
                pos_val = struct.unpack(">I", digest[:4])[0]
                s += differences[pos_val % n_pairs]
            bootstrap.append(s / n_pairs)
        bootstrap.sort()

        control_mean = sum(p["control"] for p in pairs) / n_pairs
        treatment_mean = sum(p["treatment"] for p in pairs) / n_pairs
        effect = treatment_mean - control_mean
        low = bootstrap[int(math.floor(0.025 * 4999))]
        high = bootstrap[int(math.floor(0.975 * 4999))]
        verdict = "supported" if low > 0.0 else ("falsified" if high < 0.0 else "inconclusive")

        return {
            "hypothesis": hypothesis,
            "metric": metric,
            "control_arm": control_arm,
            "treatment_arm": treatment_arm,
            "sample_size": n_pairs,
            "control_mean": control_mean,
            "treatment_mean": treatment_mean,
            "absolute_effect": effect,
            "relative_effect": None if abs(control_mean) < 1e-12 else effect / abs(control_mean),
            "confidence_low": low,
            "confidence_high": high,
            "verdict": verdict,
            "negative_result": effect <= 0.0,
            "reason_codes": ["paired_preregistered_tasks", "fixed_seeds", "bootstrap_5000", "bootstrap_sampler_v2", "negative_results_retained"],
        }

    def _insert_evaluation(self, tenant_id: int, run_id: str, evaluation: dict[str, Any]) -> None:
        ev_hash = hashlib.sha256(f"{tenant_id}|{run_id}|{evaluation['hypothesis']}".encode("utf-8")).hexdigest()
        evaluation_id = self._uuid(ev_hash)
        stmt = self.conn.prepare(
            """INSERT INTO aporia_empirical_evaluations
                (evaluation_id, tenant_id, run_id, hypothesis_key, metric_name, control_arm,
                 treatment_arm, sample_size, control_mean, treatment_mean, absolute_effect,
                 relative_effect, confidence_low, confidence_high, verdict, negative_result,
                 reason_codes_json)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            evaluation_id,
            tenant_id,
            run_id,
            evaluation["hypothesis"],
            evaluation["metric"],
            evaluation["control_arm"],
            evaluation["treatment_arm"],
            evaluation["sample_size"],
            evaluation["control_mean"],
            evaluation["treatment_mean"],
            evaluation["absolute_effect"],
            evaluation["relative_effect"],
            evaluation["confidence_low"],
            evaluation["confidence_high"],
            evaluation["verdict"],
            1 if evaluation["negative_result"] else 0,
            json.dumps(evaluation["reason_codes"], separators=(",", ":")),
        ])

    def _lock_run(self, tenant_id: int, run_id: str) -> dict[str, Any]:
        stmt = self.conn.prepare(
            "SELECT protocol_key, protocol_commitment, result_commitment, status, operator_user_id, reason_code FROM aporia_empirical_runs WHERE tenant_id = ? AND run_id = ? LIMIT 1"
        )
        stmt.execute([tenant_id, run_id])
        run = stmt.fetch()
        if not run:
            raise RuntimeError("aporia_empirical_run_unavailable")
        return run

    def _random_uuid(self) -> str:
        bytes_val = bytearray(os.urandom(16))
        bytes_val[6] = (bytes_val[6] & 0x0F) | 0x40
        bytes_val[8] = (bytes_val[8] & 0x3F) | 0x80
        hex_val = bytes_val.hex()
        return (
            hex_val[0:8]
            + "-"
            + hex_val[8:12]
            + "-"
            + hex_val[12:16]
            + "-"
            + hex_val[16:20]
            + "-"
            + hex_val[20:32]
        )

    def _uuid(self, hash_str: str) -> str:
        return (
            hash_str[0:8]
            + "-"
            + hash_str[8:12]
            + "-4"
            + hash_str[13:16]
            + "-a"
            + hash_str[17:20]
            + "-"
            + hash_str[20:32]
        )
