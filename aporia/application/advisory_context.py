"""
Advisory Context Service for APORIA.
Augments prompt contexts with scientific experimental arms, manages advisory exposures, and records outcomes.
"""

from __future__ import annotations
import re
import json
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from aporia.infrastructure.experiments import AporiaExperimentCoordinator
from aporia.infrastructure.guarded_policy import AporiaGuardedPolicy
from aporia.infrastructure.shadow_twins import AporiaShadowLifecycleLedger


class AporiaAdvisoryContextService:
    """Delivers advisory envelopes for scientific experiments and tracks outcomes."""

    def __init__(self, pdo: Any, secret: str, environment: str) -> None:
        self.pdo = pdo
        self.secret = secret
        self.environment = environment

    def augment(
        self,
        base: dict[str, Any],
        tenant_id: int,
        turn_id: str,
        mode: str,
        experiment_key: str = "aporia_c0_c5_v1",
        randomization_unit: str | None = None,
        record_exposure: bool = True,
    ) -> dict[str, Any]:
        """Augments base context with advisory envelopes according to assigned arm."""
        if mode not in ["advisory", "guarded_reversible"]:
            return base

        if not self.secret or self.secret.strip() == "" or turn_id == "":
            raise RuntimeError("aporia_advisory_context_invalid")

        started_transaction = False
        if hasattr(self.pdo, "in_transaction") and not self.pdo.in_transaction():
            self.pdo.begin_transaction()
            started_transaction = True

        try:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            assignment = AporiaExperimentCoordinator(self.pdo, self.secret).assign(
                tenant_id,
                turn_id,
                created_at,
                experiment_key,
                randomization_unit,
            )
            arm = assignment["arm"]
            delivery = "advisory" if arm in ["C2", "C4", "C5"] else "baseline"

            res = dict(base)
            if arm == "C0" and experiment_key != "aporia_real_pilot_v1":
                res["experiment_core_only"] = True

            if arm == "C3":
                res = self._forget_deterministically(res, int(assignment["seed"]))

            advisory = self._advisory(tenant_id, arm)
            envelope = {
                "delivery": delivery,
                "arm": arm,
                "advisory": advisory,
                "limitations": ["non_authoritative", "no_effect_permission", "independent_evidence_required"],
                "schema_version": 1,
            }
            envelope_commitment = hashlib.sha256(self._json(envelope).encode("utf-8")).hexdigest()
            receipt = {
                "assignment_id": assignment["assignment_id"],
                "episode_ref": assignment["episode_ref"],
                "delivery": delivery,
                "envelope_commitment": envelope_commitment,
            }
            receipt["receipt_signature"] = self._receipt_signature(tenant_id, receipt)

            if record_exposure:
                self._persist_exposure(tenant_id, turn_id, receipt, created_at)
            else:
                res["aporia_delivery_receipt"] = receipt

            if experiment_key != "aporia_real_pilot_v1":
                res["aporia_experiment"] = {
                    "arm": arm,
                    "seed": assignment["seed"],
                    "episode_ref": assignment["episode_ref"],
                    "protocol_commitment": assignment["protocol_commitment"],
                    "experiment_key": assignment["experiment_key"],
                }

            if delivery == "advisory":
                res["aporia_advisory"] = envelope

            if started_transaction and hasattr(self.pdo, "commit"):
                self.pdo.commit()

            return res
        except Exception as exc:
            if started_transaction and hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            raise exc

    def record_prepared_exposure(
        self,
        tenant_id: int,
        turn_id: str,
        receipt: dict[str, Any],
        experiment_key: str = "aporia_c0_c5_v1",
    ) -> None:
        """Persists a pre-prepared advisory exposure."""
        if tenant_id < 1 or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id):
            raise RuntimeError("aporia_advisory_exposure_invalid")

        episode_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"aporia:{tenant_id}:task:{turn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        stmt = self.pdo.prepare(
            """SELECT assignment_id FROM aporia_experiment_assignments
             WHERE tenant_id = ? AND episode_ref = ? AND experiment_key = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, episode_ref, experiment_key])
        val = stmt.fetchColumn(0)
        assignment_id = str(val or "")

        if (
            assignment_id == ""
            or not hmac.compare_digest(assignment_id, str(receipt.get("assignment_id", "")))
            or not hmac.compare_digest(episode_ref, str(receipt.get("episode_ref", "")))
            or str(receipt.get("delivery", "")) not in ["baseline", "advisory"]
            or not re.match(r"^[0-9a-f]{64}$", str(receipt.get("envelope_commitment", "")))
            or not hmac.compare_digest(self._receipt_signature(tenant_id, receipt), str(receipt.get("receipt_signature", "")))
        ):
            raise RuntimeError("aporia_advisory_exposure_invalid")

        started_transaction = False
        if hasattr(self.pdo, "in_transaction") and not self.pdo.in_transaction():
            self.pdo.begin_transaction()
            started_transaction = True

        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            self._persist_exposure(tenant_id, turn_id, receipt, now_str)
            if started_transaction and hasattr(self.pdo, "commit"):
                self.pdo.commit()
        except Exception as exc:
            if started_transaction and hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            raise exc

    def recordPreparedExposure(self, tenant_id: int, turn_id: str, receipt: dict[str, Any], experiment_key: str = "aporia_c0_c5_v1") -> None:
        self.record_prepared_exposure(tenant_id, turn_id, receipt, experiment_key)

    def record_outcome(
        self,
        tenant_id: int,
        turn_id: str,
        decision_commitment: str,
        latency_ms: int,
        cost_units: float,
        critical_regression: bool,
        experiment_key: str = "aporia_c0_c5_v1",
    ) -> dict[str, Any]:
        """Records an outcome for an advisory turn."""
        if (
            tenant_id < 1
            or turn_id == ""
            or not re.match(r"^[0-9a-f]{64}$", decision_commitment)
            or latency_ms < 0
            or cost_units < 0.0
        ):
            raise RuntimeError("aporia_advisory_outcome_invalid")

        episode_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"aporia:{tenant_id}:task:{turn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assignment_stmt = self.pdo.prepare(
            """SELECT assignment_id, arm FROM aporia_experiment_assignments
             WHERE tenant_id = ? AND episode_ref = ? AND experiment_key = ? LIMIT 1"""
        )
        assignment_stmt.execute([tenant_id, episode_ref, experiment_key])
        row = assignment_stmt.fetch()
        if not row:
            raise RuntimeError("aporia_experiment_assignment_unavailable")

        reason_codes = ["decision_commitment_observed", "cost_and_latency_observed"]
        if critical_regression:
            reason_codes.append("critical_regression_observed")
        reason_codes.append("feedback_and_counterfactual_pending")

        outcome_id = self._uuid(
            hashlib.sha256(f"{tenant_id}|{episode_ref}|advisory-outcome".encode("utf-8")).hexdigest()
        )

        started_transaction = False
        if hasattr(self.pdo, "in_transaction") and not self.pdo.in_transaction():
            self.pdo.begin_transaction()
            started_transaction = True

        try:
            inserted = self._insert_ignore(
                """INSERT INTO aporia_advisory_outcomes
                (advisory_outcome_id, tenant_id, assignment_id, episode_ref, decision_commitment,
                 baseline_decision_commitment, decision_changed, feedback_correct, confidence, brier_score,
                 calibration_error, latency_ms, cost_units, critical_regression, evaluability, reason_codes_json)
             VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, 'not_evaluable', ?)""",
                [
                    outcome_id,
                    tenant_id,
                    row["assignment_id"],
                    episode_ref,
                    decision_commitment,
                    latency_ms,
                    cost_units,
                    1 if critical_regression else 0,
                    self._json(reason_codes),
                ],
            )
            if inserted and critical_regression and experiment_key == "aporia_guarded_reversible_v1":
                AporiaGuardedPolicy(self.pdo, self.environment).stop_for_critical_regression(tenant_id)

            if started_transaction and hasattr(self.pdo, "commit"):
                self.pdo.commit()
        except Exception as exc:
            if started_transaction and hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            raise exc

        return {
            "recorded": True,
            "arm": str(row["arm"]),
            "evaluability": "not_evaluable",
            "reason_codes": reason_codes,
        }

    def recordOutcome(self, tenant_id: int, turn_id: str, decision_commitment: str, latency_ms: int, cost_units: float, critical_regression: bool, experiment_key: str = "aporia_c0_c5_v1") -> dict[str, Any]:
        return self.record_outcome(tenant_id, turn_id, decision_commitment, latency_ms, cost_units, critical_regression, experiment_key)

    def record_feedback(
        self,
        tenant_id: int,
        turn_id: str,
        helpful: bool,
        experiment_key: str = "aporia_real_pilot_v1",
    ) -> dict[str, Any]:
        """Records explicit user feedback for real-pilot trials."""
        if tenant_id < 1 or turn_id == "" or experiment_key != "aporia_real_pilot_v1":
            raise RuntimeError("aporia_advisory_feedback_invalid")

        episode_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"aporia:{tenant_id}:task:{turn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        started_transaction = False
        if hasattr(self.pdo, "in_transaction") and not self.pdo.in_transaction():
            self.pdo.begin_transaction()
            started_transaction = True

        try:
            stmt = self.pdo.prepare(
                """SELECT a.assignment_id, a.arm, o.feedback_correct
             FROM aporia_experiment_assignments a
             INNER JOIN aporia_advisory_outcomes o
                ON o.tenant_id = a.tenant_id AND o.assignment_id = a.assignment_id
             WHERE a.tenant_id = ? AND a.episode_ref = ? AND a.experiment_key = ? LIMIT 1"""
            )
            stmt.execute([tenant_id, episode_ref, experiment_key])
            row = stmt.fetch()
            if not row:
                raise RuntimeError("aporia_advisory_outcome_unavailable")

            existing = row.get("feedback_correct")
            if existing is not None and bool(existing) != helpful:
                raise RuntimeError("aporia_advisory_feedback_conflict")

            updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            reason_codes = [
                "decision_commitment_observed",
                "cost_and_latency_observed",
                "explicit_helpful_feedback" if helpful else "explicit_not_helpful_feedback",
            ]
            update = self.pdo.prepare(
                """UPDATE aporia_advisory_outcomes
                 SET feedback_correct = ?, evaluability = 'observed', reason_codes_json = ?, updated_at = ?
                 WHERE tenant_id = ? AND assignment_id = ? AND feedback_correct IS NULL"""
            )
            update.execute([
                1 if helpful else 0,
                self._json(reason_codes),
                updated_at,
                tenant_id,
                row["assignment_id"],
            ])

            operation_id = hashlib.sha256(f"{tenant_id}|{episode_ref}|explicit-feedback".encode("utf-8")).hexdigest()
            feedback_id = self._uuid(hashlib.sha256(f"{operation_id}|feedback".encode("utf-8")).hexdigest())
            
            self._insert_ignore(
                """INSERT INTO aporia_real_pilot_feedback_events
                    (feedback_id, tenant_id, assignment_id, episode_ref, feedback_correct, source,
                     event_name, event_version, environment, stream, category, component, operation_id,
                     actor_type, action_name, lifecycle_phase, outcome, reason_code, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, 'sync_user', 'aporia.real_pilot.feedback.recorded', 1, ?, 'user', 'audit',
                         'aporia-real-pilot', ?, 'user', 'record_feedback', 'succeeded', 'succeeded', ?, ?, ?)""",
                [
                    feedback_id,
                    tenant_id,
                    row["assignment_id"],
                    episode_ref,
                    1 if helpful else 0,
                    self.environment[:24],
                    operation_id,
                    "explicit_helpful_feedback" if helpful else "explicit_not_helpful_feedback",
                    updated_at,
                    updated_at,
                ],
            )

            if started_transaction and hasattr(self.pdo, "commit"):
                self.pdo.commit()

            return {
                "recorded": True,
                "deduplicated": existing is not None,
                "arm": str(row["arm"]),
                "evaluability": "observed",
            }
        except Exception as exc:
            if started_transaction and hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            raise exc

    def recordFeedback(self, tenant_id: int, turn_id: str, helpful: bool, experiment_key: str = "aporia_real_pilot_v1") -> dict[str, Any]:
        return self.record_feedback(tenant_id, turn_id, helpful, experiment_key)

    def _advisory(self, tenant_id: int, arm: str) -> dict[str, Any]:
        if arm == "C2":
            identity = self._latest_identity(tenant_id)
            return {
                "identity_continuity": identity,
                "evaluability": "passive_introspection",
            }
        if arm not in ["C4", "C5"]:
            return {}

        stmt = self.pdo.prepare(
            """SELECT classification, world_score, corrected_self_score, cycle_score, reason_codes_json, created_at
             FROM aporia_obstruction_measurements WHERE tenant_id = ? ORDER BY id DESC LIMIT 1"""
        )
        stmt.execute([tenant_id])
        obstruction = stmt.fetch()
        result: dict[str, Any] = {
            "identity_continuity": self._latest_identity(tenant_id),
            "obstruction": None,
            "hirt_enabled": arm == "C5",
        }
        if obstruction:
            result["obstruction"] = {
                "classification": str(obstruction["classification"]),
                "world_score": float(obstruction["world_score"]),
                "corrected_self_score": float(obstruction["corrected_self_score"]),
                "cycle_score": float(obstruction["cycle_score"]),
                "reason_codes": json.loads(str(obstruction.get("reason_codes_json", "[]"))),
                "observed_at": str(obstruction["created_at"]),
            }
        return result

    def _latest_identity(self, tenant_id: int) -> dict[str, Any] | None:
        stmt = self.pdo.prepare(
            """SELECT continuity_score, model_dependency, graph_count, behavior_count, commitment_count,
                    autobiography_count, ontology_count, created_at
             FROM aporia_identity_snapshots WHERE tenant_id = ? ORDER BY id DESC LIMIT 1"""
        )
        stmt.execute([tenant_id])
        row = stmt.fetch()
        if not row:
            return None

        return {
            "continuity_score": float(row["continuity_score"]),
            "model_dependency": str(row["model_dependency"]),
            "component_counts": {
                "graph": int(row["graph_count"]),
                "behavior": int(row["behavior_count"]),
                "commitment": int(row["commitment_count"]),
                "autobiography": int(row["autobiography_count"]),
                "ontology": int(row["ontology_count"]),
            },
            "observed_at": str(row["created_at"]),
        }

    def _forget_deterministically(self, base: dict[str, Any], seed: int) -> dict[str, Any]:
        evidence = dict(base.get("evidence", {})) if isinstance(base.get("evidence"), dict) else {}
        keys = sorted(list(evidence.keys()))
        if not keys:
            return base

        forgotten = keys[seed % len(keys)]
        del evidence[forgotten]
        res = dict(base)
        res["evidence"] = evidence
        res["aporia_random_forgetting"] = {"omitted_fields": 1, "registered_seed": seed}
        return res

    def _insert_ignore(self, sql: str, parameters: list[Any]) -> bool:
        driver = getattr(self.pdo, "getAttribute", lambda x: "sqlite")(None)
        replacement = "INSERT OR IGNORE INTO " if driver == "sqlite" else "INSERT IGNORE INTO "
        final_sql = re.sub(r"^INSERT INTO ", replacement, sql, count=1)
        stmt = self.pdo.prepare(final_sql)
        stmt.execute(parameters)
        return stmt.rowcount == 1

    def _persist_exposure(self, tenant_id: int, turn_id: str, receipt: dict[str, Any], created_at: str) -> None:
        operation_id = hashlib.sha256(f"{tenant_id}|{receipt['episode_ref']}|advisory-exposure".encode("utf-8")).hexdigest()
        self._insert_ignore(
            """INSERT INTO aporia_advisory_exposures
                (exposure_id, tenant_id, assignment_id, episode_ref, delivery, envelope_commitment,
                 event_name, event_version, environment, stream, category, component, operation_id,
                 actor_type, action_name, lifecycle_phase, outcome, reason_code, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, 'aporia.advisory.envelope.delivered', 1, ?, 'system', 'audit',
                     'aporia-advisory-context', ?, 'worker', 'deliver_advisory_envelope', 'succeeded',
                     'succeeded', ?, ?, ?)""",
            [
                self._uuid(hashlib.sha256(f"{operation_id}|exposure".encode("utf-8")).hexdigest()),
                tenant_id,
                receipt["assignment_id"],
                receipt["episode_ref"],
                receipt["delivery"],
                receipt["envelope_commitment"],
                self.environment[:24],
                operation_id,
                "advisory_delivered" if receipt["delivery"] == "advisory" else "baseline_assigned",
                created_at,
                created_at,
            ],
        )
        AporiaShadowLifecycleLedger(self.pdo, self.secret, self.environment).expose_and_seal_predictions(tenant_id, turn_id)

    def _receipt_signature(self, tenant_id: int, receipt: dict[str, Any]) -> str:
        parts = [
            str(tenant_id),
            str(receipt.get("assignment_id", "")),
            str(receipt.get("episode_ref", "")),
            str(receipt.get("delivery", "")),
            str(receipt.get("envelope_commitment", "")),
        ]
        msg = "|".join(parts).encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _uuid(self, hash_str: str) -> str:
        return f"{hash_str[0:8]}-{hash_str[8:12]}-4{hash_str[13:16]}-a{hash_str[17:20]}-{hash_str[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
