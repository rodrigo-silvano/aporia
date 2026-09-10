"""Ecological outcome recorder, prospective program, and transport preconditions for Aporia."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Any

from aporia.infrastructure.db import Connection


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_dt(dt_str: str) -> datetime:
    try:
        # Handles ISO format, with or without microseconds
        clean = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        # Fallback to strptime
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                pass
        raise RuntimeError("aporia_ecological_datetime_invalid")


class AporiaEcologicalTransportPrecondition:
    ACCOUNT_ID = 49

    def __init__(self, conn: Connection, secret: str, environment: str):
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment

    def attest_safely(
        self,
        tenant_id: int,
        session_public_id: str,
        turn_id: str,
        authenticated_websocket: bool,
        request_id: str = "",
    ) -> dict[str, Any]:
        try:
            return self.attest(tenant_id, session_public_id, turn_id, authenticated_websocket, request_id)
        except Exception:
            return {"verified": False, "reason_codes": ["ecological_transport_precondition_unavailable"]}

    def attest(
        self,
        tenant_id: int,
        session_public_id: str,
        turn_id: str,
        authenticated_websocket: bool,
        request_id: str = "",
    ) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"verified": False, "reason_codes": ["ecological_transport_scope_inactive"]}

        if (
            not authenticated_websocket
            or not self.secret
            or not re.match(r"^[0-9a-f-]{36}$", session_public_id, re.IGNORECASE)
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id)
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", request_id)
        ):
            raise RuntimeError("aporia_ecological_transport_attestation_invalid")

        binding = self.conn.prepare(
            """SELECT session.id
             FROM assistant_runtime_sessions session
             INNER JOIN assistant_runtime_runs runtime_run
                     ON runtime_run.session_id = session.id
                    AND runtime_run.turn_id = ?
                    AND runtime_run.request_id = ?
                    AND runtime_run.status IN ('accepted','running')
             WHERE session.public_id = ? AND session.owner_user_id = ? AND session.status = 'active'
             LIMIT 1"""
        )
        binding.execute([turn_id, request_id, session_public_id, tenant_id])
        if not binding.fetch():
            raise RuntimeError("aporia_ecological_session_turn_binding_invalid")

        turn_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        session_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-ecological-session|{tenant_id}|{session_public_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        request_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-ecological-request|{tenant_id}|{request_id}".encode("utf-8"), hashlib.sha256).hexdigest()

        attested_at = _now_str()
        attestation_msg = f"{tenant_id}|{session_ref}|{turn_ref}|{request_ref}|authenticated_websocket|session_turn_binding"
        attestation_hash = hmac.new(self.secret.encode("utf-8"), attestation_msg.encode("utf-8"), hashlib.sha256).hexdigest()

        op_id = hashlib.sha256(f"{tenant_id}|{turn_ref}|ecological-transport-precondition-r1".encode("utf-8")).hexdigest()
        event_id = hashlib.sha256(f"{op_id}|aporia.ecological.transport.verified|1".encode("utf-8")).hexdigest()

        stmt = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_ecological_transport_preconditions_r1
                (event_id, tenant_id, session_ref, turn_ref, request_ref,
                 authenticated_websocket, session_turn_binding, attested_at,
                 attestation_hash, event_name, event_version, occurred_at,
                 environment, stream, category, component, operation_id,
                 actor_type, initiator_type, executor_type, source_channel,
                 target_type, action_name, lifecycle_phase, outcome, reason_code,
                 created_at)
             VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            event_id,
            tenant_id,
            session_ref,
            turn_ref,
            request_ref,
            attested_at,
            attestation_hash,
            "aporia.ecological.transport.verified",
            attested_at,
            self.environment[:24],
            "system",
            "audit",
            "aporia-ecological-prospective",
            op_id,
            "system",
            "user",
            "service",
            "authenticated_websocket",
            "ecological_turn",
            "verify_transport_preconditions",
            "succeeded",
            "succeeded",
            "authenticated_websocket_and_session_turn_binding",
            attested_at,
        ])

        if stmt.row_count == 0:
            stored = self._row(tenant_id, session_ref, turn_ref)
            if (
                int(stored.get("authenticated_websocket", 0)) != 1
                or int(stored.get("session_turn_binding", 0)) != 1
                or not hmac.compare_digest(str(stored.get("request_ref", "")), request_ref)
                or not hmac.compare_digest(str(stored.get("attestation_hash", "")), attestation_hash)
            ):
                raise RuntimeError("aporia_ecological_transport_attestation_conflict")
            return {"verified": True, "new_record": False, "attestation_hash": attestation_hash}

        return {"verified": True, "new_record": True, "attestation_hash": attestation_hash}

    def verified(self, tenant_id: int, session_public_id: str, turn_id: str) -> bool:
        return len(self.evidence(tenant_id, session_public_id, turn_id)) > 0

    def evidence(self, tenant_id: int, session_public_id: str, turn_id: str) -> dict[str, Any]:
        if not self._active(tenant_id) or not self.secret:
            return {}
        turn_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        session_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-ecological-session|{tenant_id}|{session_public_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        stored = self._row(tenant_id, session_ref, turn_ref)
        if int(stored.get("authenticated_websocket", 0)) != 1 or int(stored.get("session_turn_binding", 0)) != 1:
            return {}
        expected_msg = f"{tenant_id}|{session_ref}|{turn_ref}|{stored.get('request_ref', '')}|authenticated_websocket|session_turn_binding"
        expected = hmac.new(self.secret.encode("utf-8"), expected_msg.encode("utf-8"), hashlib.sha256).hexdigest()
        return stored if hmac.compare_digest(str(stored.get("attestation_hash", "")), expected) else {}

    def _row(self, tenant_id: int, session_ref: str, turn_ref: str) -> dict[str, Any]:
        stmt = self.conn.prepare(
            """SELECT request_ref, authenticated_websocket, session_turn_binding, attestation_hash
             FROM aporia_ecological_transport_preconditions_r1
             WHERE tenant_id = ? AND session_ref = ? AND turn_ref = ?
             LIMIT 1"""
        )
        stmt.execute([tenant_id, session_ref, turn_ref])
        return stmt.fetch() or {}

    def _active(self, tenant_id: int) -> bool:
        return self.environment.strip().lower() == "staging" and tenant_id == self.ACCOUNT_ID


class AporiaEcologicalOutcomeRecorder:
    ACCOUNT_ID = 49

    def __init__(self, conn: Connection, secret: str, environment: str, logger: Any = None):
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment
        self.logger = logger

    def record_safely(self, tenant_id: int, relationship_id: int, revision: int, outcome: str, outcome_at: str | None) -> dict[str, Any]:
        try:
            return self.record(tenant_id, relationship_id, revision, outcome, outcome_at)
        except Exception:
            return {"recorded": False, "reason_codes": ["ecological_outcome_unavailable"]}

    def record(self, tenant_id: int, relationship_id: int, revision: int, outcome: str, outcome_at: str | None) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"recorded": False, "reason_codes": ["ecological_scope_inactive"]}
        if relationship_id < 1 or revision < 1 or outcome not in ("won", "lost") or not outcome_at or not self.secret:
            raise RuntimeError("aporia_ecological_outcome_invalid")

        try:
            outcome_time = _parse_dt(outcome_at)
        except Exception:
            raise RuntimeError("aporia_ecological_outcome_invalid")

        rows = self._episodes(tenant_id, relationship_id)
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        recorded = 0
        idempotent = 0
        try:
            for row in rows:
                self._assert_episode(row, outcome_time)
                rel_ref = hmac.new(self.secret.encode("utf-8"), f"relationship|{tenant_id}|{relationship_id}".encode("utf-8"), hashlib.sha256).hexdigest()
                sess_ref = hmac.new(self.secret.encode("utf-8"), f"session|{tenant_id}|{row['session_public_id']}".encode("utf-8"), hashlib.sha256).hexdigest()
                time_str = outcome_time.strftime("%Y-%m-%d %H:%M:%S.%f")
                c_msg = f"commercial-outcome-v2|{tenant_id}|{rel_ref}|{revision}|{outcome}|{time_str}"
                outcome_commitment = hmac.new(self.secret.encode("utf-8"), c_msg.encode("utf-8"), hashlib.sha256).hexdigest()

                op_id = hashlib.sha256(f"{tenant_id}|{row['turn_ref']}|{rel_ref}|{revision}|{outcome}".encode("utf-8")).hexdigest()
                ev_id = self._uuid(op_id)

                params = [
                    ev_id,
                    tenant_id,
                    rel_ref,
                    revision,
                    str(row["turn_ref"]),
                    sess_ref,
                    str(row["c0_observation_commitment"]),
                    str(row["c0_prediction_commitment"]),
                    str(row["c5_observation_commitment"]),
                    str(row["c5_prediction_commitment"]),
                    outcome,
                    outcome_commitment,
                    time_str,
                    self.environment[:24],
                    op_id,
                ]
                stmt = self.conn.prepare(
                    """INSERT OR IGNORE INTO aporia_ecological_episode_outcomes_r2
                        (event_id, tenant_id, relationship_ref, relationship_revision, turn_ref, session_ref,
                         c0_observation_commitment, c0_prediction_commitment, c5_observation_commitment,
                         c5_prediction_commitment, outcome_label, outcome_commitment, outcome_at,
                         same_observation, predictions_before_outcome, runtime_influence, external_effect,
                         contamination_detected, event_name, event_version, environment, stream, category,
                         component, operation_id, actor_type, target_type, action_name, lifecycle_phase,
                         outcome, reason_code, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, 0, 0,
                             'aporia.ecological.commercial_outcome.recorded', 2, ?, 'system', 'audit',
                             'aporia-ecological-shadow', ?, 'system', 'shadow_episode',
                             'record_commercial_outcome', 'succeeded', 'succeeded',
                             'observable_outcome_after_prediction', CURRENT_TIMESTAMP)"""
                )
                stmt.execute(params)
                if stmt.row_count == 1:
                    recorded += 1
                    continue

                check_stmt = self.conn.prepare(
                    "SELECT outcome_commitment FROM aporia_ecological_episode_outcomes_r2 WHERE tenant_id = ? AND operation_id = ? LIMIT 1"
                )
                check_stmt.execute([tenant_id, op_id])
                stored = check_stmt.fetch_column()
                if not stored or not hmac.compare_digest(str(stored), outcome_commitment):
                    raise RuntimeError("aporia_ecological_outcome_conflict")
                idempotent += 1

            if started:
                self.conn.commit()
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

        return {
            "recorded": True,
            "matched_episodes": len(rows),
            "inserted_episodes": recorded,
            "idempotent_episodes": idempotent,
            "runtime_influence": False,
            "external_effects": False,
            "contamination": False,
        }

    def _episodes(self, tenant_id: int, relationship_id: int) -> list[dict[str, Any]]:
        stmt = self.conn.prepare(
            """SELECT lifecycle.turn_ref, lifecycle.session_public_id,
                    lifecycle.c0_prediction_sealed_at, lifecycle.c5_prediction_sealed_at,
                    c0.observation_commitment AS c0_observation_commitment,
                    c0.prediction_commitment AS c0_prediction_commitment,
                    c0.assigned_before_outcome AS c0_assigned_before_outcome,
                    c0.runtime_influence AS c0_runtime_influence,
                    c0.external_effect AS c0_external_effect,
                    c0.contamination_detected AS c0_contamination_detected,
                    c0.created_at AS c0_created_at,
                    c5.observation_commitment AS c5_observation_commitment,
                    c5.prediction_commitment AS c5_prediction_commitment,
                    c5.assigned_before_outcome AS c5_assigned_before_outcome,
                    c5.runtime_influence AS c5_runtime_influence,
                    c5.external_effect AS c5_external_effect,
                    c5.contamination_detected AS c5_contamination_detected,
                    c5.created_at AS c5_created_at
             FROM assistant_runtime_sessions session
             INNER JOIN aporia_shadow_episode_lifecycle lifecycle
                     ON lifecycle.tenant_id = session.owner_user_id
                    AND lifecycle.session_public_id = session.public_id
             INNER JOIN aporia_longitudinal_shadow_c0 c0
                     ON c0.tenant_id = lifecycle.tenant_id AND c0.turn_ref = lifecycle.turn_ref
             INNER JOIN aporia_longitudinal_shadow_c5 c5
                     ON c5.tenant_id = lifecycle.tenant_id AND c5.turn_ref = lifecycle.turn_ref
             WHERE session.owner_user_id = ? AND session.relationship_id = ?
             ORDER BY lifecycle.id"""
        )
        stmt.execute([tenant_id, relationship_id])
        return stmt.fetch_all() or []

    def _assert_episode(self, row: dict[str, Any], outcome_time: datetime) -> None:
        same_obs = hmac.compare_digest(str(row["c0_observation_commitment"]), str(row["c5_observation_commitment"]))
        safe_flags = [
            int(row["c0_assigned_before_outcome"]),
            int(row["c5_assigned_before_outcome"]),
            1 - int(row["c0_runtime_influence"]),
            1 - int(row["c5_runtime_influence"]),
            1 - int(row["c0_external_effect"]),
            1 - int(row["c5_external_effect"]),
            1 - int(row["c0_contamination_detected"]),
            1 - int(row["c5_contamination_detected"]),
        ]
        timestamps = [
            row.get("c0_prediction_sealed_at"),
            row.get("c5_prediction_sealed_at"),
            row.get("c0_created_at"),
            row.get("c5_created_at"),
        ]
        try:
            before_outcome = all(isinstance(ts, str) and _parse_dt(ts) <= outcome_time for ts in timestamps)
        except Exception:
            before_outcome = False

        if not same_obs or min(safe_flags) != 1 or not before_outcome:
            raise RuntimeError("aporia_ecological_episode_invalid")

    def _active(self, tenant_id: int) -> bool:
        return tenant_id == self.ACCOUNT_ID and self.environment.strip().lower() == "staging"

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


class AporiaProspectiveEcologicalProgram:
    ACCOUNT_ID = 49
    ELIGIBILITY_VERSION = "aporia_ecological_prospective_v1"
    QUARTILE_MODEL_VERSION = "obstruction_or_relationship_score_v1"

    def __init__(self, conn: Connection, secret: str, environment: str):
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment

    def enroll_safely(self, tenant_id: int, relationship_id: int, relationship_score: int | None) -> dict[str, Any]:
        try:
            return self.enroll(tenant_id, relationship_id, relationship_score)
        except Exception:
            return {"enrolled": False, "reason_codes": ["prospective_enrollment_unavailable"]}

    def enroll(self, tenant_id: int, relationship_id: int, relationship_score: int | None) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"enrolled": False, "reason_codes": ["prospective_scope_inactive"]}
        if relationship_id < 1 or not self.secret:
            raise RuntimeError("aporia_prospective_enrollment_invalid")

        quartile = self._baseline_quartile(tenant_id, relationship_score)
        relation_ref = self._opaque(tenant_id, "relationship", str(relationship_id))
        c0_lineage = self._opaque(tenant_id, "C0-lineage", relation_ref)
        c5_lineage = self._opaque(tenant_id, "C5-lineage", relation_ref)
        state_c0 = hmac.new(self.secret.encode("utf-8"), f"C0|initial|{relation_ref}".encode("utf-8"), hashlib.sha256).hexdigest()
        state_c5 = hmac.new(self.secret.encode("utf-8"), f"C5|initial|{relation_ref}".encode("utf-8"), hashlib.sha256).hexdigest()
        enrolled_at = _now_str()

        op_id = hashlib.sha256(f"{tenant_id}|{relation_ref}|{self.ELIGIBILITY_VERSION}".encode("utf-8")).hexdigest()
        eligible = quartile is not None

        stmt = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_ecological_relations_r3
                (relation_ref, tenant_id, relationship_id, eligibility_version, enrolled_at,
                 baseline_quartile, quartile_model_version, c0_lineage, c5_lineage,
                 c0_state_hash, c5_state_hash, enrollment_status, provenance_complete,
                 event_name, event_version, environment, stream, category, component,
                 operation_id, actor_type, target_type, action_name, lifecycle_phase,
                 outcome, reason_code, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            relation_ref,
            tenant_id,
            relationship_id,
            self.ELIGIBILITY_VERSION,
            enrolled_at,
            quartile,
            self.QUARTILE_MODEL_VERSION,
            c0_lineage,
            c5_lineage,
            state_c0,
            state_c5,
            "enrolled" if eligible else "ineligible_missing_baseline",
            1 if eligible else 0,
            "aporia.ecological.relation.enrolled",
            self.environment[:24],
            "system",
            "audit",
            "aporia-ecological-prospective",
            op_id,
            "system",
            "ecological_relation",
            "enroll_relation",
            "succeeded",
            "succeeded",
            "prospective_baseline_frozen" if eligible else "baseline_unavailable_before_outcome",
            enrolled_at,
            enrolled_at,
        ])

        if stmt.row_count == 0:
            stored = self._relation(tenant_id, relationship_id)
            stored_q = None if stored.get("baseline_quartile") is None else int(stored["baseline_quartile"])
            if (
                str(stored.get("relation_ref", "")) != relation_ref
                or str(stored.get("eligibility_version", "")) != self.ELIGIBILITY_VERSION
                or stored_q != quartile
                or str(stored.get("quartile_model_version", "")) != self.QUARTILE_MODEL_VERSION
                or str(stored.get("c0_lineage", "")) != c0_lineage
                or str(stored.get("c5_lineage", "")) != c5_lineage
                or str(stored.get("c0_state_hash", "")) != state_c0
                or str(stored.get("c5_state_hash", "")) != state_c5
            ):
                raise RuntimeError("aporia_prospective_enrollment_conflict")
            return {
                "enrolled": True,
                "eligible": int(stored.get("provenance_complete", 0)) == 1,
                "relation_ref": str(stored["relation_ref"]),
                "baseline_quartile": stored_q,
                "new_record": False,
            }

        return {
            "enrolled": True,
            "eligible": eligible,
            "relation_ref": relation_ref,
            "baseline_quartile": quartile,
            "new_record": True,
        }

    def seal_prediction_safely(
        self,
        tenant_id: int,
        relationship_id: int,
        turn_id: str,
        session_public_id: str,
    ) -> dict[str, Any]:
        try:
            return self.seal_prediction(tenant_id, relationship_id, turn_id, session_public_id)
        except Exception:
            return {"sealed": False, "reason_codes": ["prospective_prediction_unavailable"]}

    def seal_prediction(self, tenant_id: int, relationship_id: int, turn_id: str, session_public_id: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"sealed": False, "reason_codes": ["prospective_scope_inactive"]}
        relation = self._relation(tenant_id, relationship_id)
        if not relation or int(relation.get("provenance_complete", 0)) != 1:
            return {"sealed": False, "reason_codes": ["prospective_relation_ineligible"]}

        turn_ref = hmac.new(self.secret.encode("utf-8"), f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}".encode("utf-8"), hashlib.sha256).hexdigest()

        binding = self.conn.prepare(
            """SELECT 1
             FROM aporia_shadow_episode_lifecycle lifecycle
             INNER JOIN assistant_runtime_sessions runtime_session
                     ON runtime_session.public_id = lifecycle.session_public_id
                    AND runtime_session.owner_user_id = lifecycle.tenant_id
             WHERE lifecycle.tenant_id = ? AND lifecycle.turn_ref = ?
               AND lifecycle.session_public_id = ? AND runtime_session.relationship_id = ?
             LIMIT 1"""
        )
        binding.execute([tenant_id, turn_ref, session_public_id, relationship_id])
        if not binding.fetch():
            raise RuntimeError("aporia_prospective_session_relationship_mismatch")

        transport_evidence = AporiaEcologicalTransportPrecondition(self.conn, self.secret, self.environment).evidence(tenant_id, session_public_id, turn_id)
        if not transport_evidence:
            raise RuntimeError("aporia_ecological_transport_precondition_missing")
        transport_attestation_hash = str(transport_evidence["attestation_hash"])

        stmt = self.conn.prepare(
            """SELECT c0.observation_commitment, c0.prediction_label AS c0_prediction_label,
                    c0.prediction_commitment AS c0_prediction_commitment,
                    c5.prediction_label AS c5_prediction_label,
                    c5.prediction_commitment AS c5_prediction_commitment,
                    c0.created_at AS c0_created_at, c5.created_at AS c5_created_at
             FROM aporia_longitudinal_shadow_c0 c0
             INNER JOIN aporia_longitudinal_shadow_c5 c5
                     ON c5.tenant_id = c0.tenant_id AND c5.turn_ref = c0.turn_ref
                    AND c5.observation_commitment = c0.observation_commitment
             WHERE c0.tenant_id = ? AND c0.turn_ref = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, turn_ref])
        prediction = stmt.fetch()
        if not prediction:
            raise RuntimeError("aporia_prospective_prediction_pair_missing")

        sealed_at = _now_str()
        enrolled_at = _parse_dt(str(relation["enrolled_at"]))
        if (
            enrolled_at > _parse_dt(sealed_at)
            or _parse_dt(str(prediction["c0_created_at"])) < enrolled_at
            or _parse_dt(str(prediction["c5_created_at"])) < enrolled_at
        ):
            raise RuntimeError("aporia_prospective_prediction_before_enrollment")

        c0_prob = self._win_probability(str(prediction["c0_prediction_label"]))
        c5_prob = self._win_probability(str(prediction["c5_prediction_label"]))

        seal_msg = f"{relation['relation_ref']}|{turn_ref}|{transport_attestation_hash}|{prediction['observation_commitment']}|{prediction['c0_prediction_commitment']}|{prediction['c5_prediction_commitment']}|{c0_prob}|{c5_prob}|{sealed_at}"
        seal_hash = hmac.new(self.secret.encode("utf-8"), seal_msg.encode("utf-8"), hashlib.sha256).hexdigest()

        op_id = hashlib.sha256(f"{tenant_id}|{relation['relation_ref']}|first-prediction-seal".encode("utf-8")).hexdigest()

        insert = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_ecological_predictions_r3
                (tenant_id, relation_ref, turn_ref, observation_commitment,
                 c0_prediction_label, c0_win_probability, c0_prediction_commitment,
                 c5_prediction_label, c5_win_probability, c5_prediction_commitment,
                 prediction_sealed_at, prediction_seal_hash, transport_attestation_hash,
                 same_observation, separate_stores, runtime_influence, external_effect,
                 contamination_detected, event_name, event_version, environment, stream,
                 category, component, operation_id, actor_type, target_type, action_name,
                 lifecycle_phase, outcome, reason_code, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, 0, 0,
                     ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        insert.execute([
            tenant_id,
            relation["relation_ref"],
            turn_ref,
            prediction["observation_commitment"],
            prediction["c0_prediction_label"],
            c0_prob,
            prediction["c0_prediction_commitment"],
            prediction["c5_prediction_label"],
            c5_prob,
            prediction["c5_prediction_commitment"],
            sealed_at,
            seal_hash,
            transport_attestation_hash,
            "aporia.ecological.prediction.sealed",
            self.environment[:24],
            "system",
            "audit",
            "aporia-ecological-prospective",
            op_id,
            "worker",
            "ecological_relation",
            "seal_shadow_prediction",
            "succeeded",
            "succeeded",
            "paired_prediction_before_outcome",
            sealed_at,
        ])

        if insert.row_count == 1:
            return {"sealed": True, "new_record": True, "prediction_seal_hash": seal_hash}

        stored = self._prediction(tenant_id, str(relation["relation_ref"]))
        expected = {
            "turn_ref": turn_ref,
            "observation_commitment": str(prediction["observation_commitment"]),
            "c0_prediction_label": str(prediction["c0_prediction_label"]),
            "c0_prediction_commitment": str(prediction["c0_prediction_commitment"]),
            "c5_prediction_label": str(prediction["c5_prediction_label"]),
            "c5_prediction_commitment": str(prediction["c5_prediction_commitment"]),
            "transport_attestation_hash": transport_attestation_hash,
        }
        for field, val in expected.items():
            if str(stored.get(field, "")) != val:
                raise RuntimeError("aporia_prospective_prediction_conflict")

        if (
            abs(float(stored.get("c0_win_probability", -1)) - c0_prob) > 1e-6
            or abs(float(stored.get("c5_win_probability", -1)) - c5_prob) > 1e-6
        ):
            raise RuntimeError("aporia_prospective_prediction_conflict")

        return {
            "sealed": True,
            "new_record": False,
            "prediction_seal_hash": str(stored["prediction_seal_hash"]),
        }

    def record_terminal_safely(
        self,
        tenant_id: int,
        relationship_id: int,
        actual_action_source: str,
        actual_action: str,
        terminal_outcome: str,
        outcome_at: str,
    ) -> dict[str, Any]:
        try:
            return self.record_terminal(tenant_id, relationship_id, actual_action_source, actual_action, terminal_outcome, outcome_at)
        except Exception:
            return {"recorded": False, "reason_codes": ["prospective_outcome_unavailable"]}

    def record_terminal(
        self,
        tenant_id: int,
        relationship_id: int,
        actual_action_source: str,
        actual_action: str,
        terminal_outcome: str,
        outcome_at: str,
    ) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"recorded": False, "reason_codes": ["prospective_scope_inactive"]}
        if (
            terminal_outcome not in ("won", "lost")
            or not re.match(r"^[a-z0-9_-]{2,32}$", actual_action_source)
            or not re.match(r"^[a-z0-9_-]{2,64}$", actual_action)
        ):
            raise RuntimeError("aporia_prospective_outcome_invalid")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            relation = self._relation(tenant_id, relationship_id, True)
            if not relation:
                if started:
                    self.conn.commit()
                return {"recorded": False, "reason_codes": ["prospective_relation_not_enrolled"]}

            if str(relation.get("enrollment_status")) == "censored":
                raise RuntimeError("aporia_prospective_outcome_after_censor")

            prediction = self._prediction(tenant_id, str(relation["relation_ref"]))
            if not prediction:
                if started:
                    self.conn.commit()
                return {"recorded": False, "reason_codes": ["prediction_not_sealed"]}

            if not self._design(tenant_id, True):
                raise RuntimeError("aporia_ecological_design_missing")

            outcome_time = _parse_dt(outcome_at)
            if _parse_dt(str(prediction["prediction_sealed_at"])) >= outcome_time:
                raise RuntimeError("aporia_prospective_late_prediction")

            time_str = outcome_time.strftime("%Y-%m-%d %H:%M:%S.%f")
            out_msg = f"{relation['relation_ref']}|{actual_action_source}|{actual_action}|{terminal_outcome}|{time_str}"
            outcome_commitment = hmac.new(self.secret.encode("utf-8"), out_msg.encode("utf-8"), hashlib.sha256).hexdigest()
            op_id = hashlib.sha256(f"{tenant_id}|{relation['relation_ref']}|terminal-outcome".encode("utf-8")).hexdigest()

            stmt = self.conn.prepare(
                """INSERT OR IGNORE INTO aporia_ecological_terminal_outcomes_r3
                    (tenant_id, relation_ref, actual_action_source, actual_action, terminal_definition,
                     terminal_outcome, outcome_timestamp, censoring_reason, outcome_commitment,
                     adjudication_blinded_to_arm, provenance_complete, event_name, event_version,
                     environment, stream, category, component, operation_id, actor_type, target_type,
                     action_name, lifecycle_phase, outcome, reason_code, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 1, 1, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)"""
            )
            stmt.execute([
                tenant_id,
                relation["relation_ref"],
                actual_action_source,
                actual_action,
                "commercial_stage_won_or_lost_v1",
                terminal_outcome,
                time_str,
                outcome_commitment,
                "aporia.ecological.outcome.adjudicated",
                self.environment[:24],
                "system",
                "audit",
                "aporia-ecological-prospective",
                op_id,
                "system",
                "ecological_relation",
                "adjudicate_terminal_outcome",
                "succeeded",
                "succeeded",
                "blinded_terminal_outcome_after_prediction",
            ])

            new_record = stmt.row_count == 1
            if not new_record:
                stored = self._terminal_outcome(tenant_id, str(relation["relation_ref"]))
                if not hmac.compare_digest(str(stored.get("outcome_commitment", "")), outcome_commitment):
                    raise RuntimeError("aporia_prospective_outcome_conflict")

            term_stmt = self.conn.prepare(
                """UPDATE aporia_ecological_relations_r3
                 SET enrollment_status = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE tenant_id = ? AND relation_ref = ? AND enrollment_status NOT IN (?, ?)"""
            )
            term_stmt.execute(["terminal", tenant_id, relation["relation_ref"], "terminal", "censored"])

            if started:
                self.conn.commit()
            return {"recorded": True, "new_record": new_record, "outcome_commitment": outcome_commitment}
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def censor(self, tenant_id: int, relationship_id: int, reason_code: str, censored_at: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"censored": False, "reason_codes": ["prospective_scope_inactive"]}
        if not re.match(r"^[a-z0-9_]{3,64}$", reason_code):
            raise RuntimeError("aporia_prospective_censor_invalid")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            relation = self._relation(tenant_id, relationship_id, True)
            if not relation:
                if started:
                    self.conn.commit()
                return {"censored": False, "reason_codes": ["prospective_relation_not_enrolled"]}

            if self._terminal_outcome(tenant_id, str(relation["relation_ref"])):
                raise RuntimeError("aporia_prospective_censor_after_outcome")

            norm_censored_at = _parse_dt(censored_at).strftime("%Y-%m-%d %H:%M:%S.%f")
            if str(relation.get("enrollment_status")) == "censored":
                stored_c_at = _parse_dt(str(relation.get("censored_at"))).strftime("%Y-%m-%d %H:%M:%S.%f")
                if str(relation.get("censoring_reason")) != reason_code or stored_c_at != norm_censored_at:
                    raise RuntimeError("aporia_prospective_censor_conflict")
                if started:
                    self.conn.commit()
                return {"censored": True, "changed": False}

            if str(relation.get("enrollment_status")) == "terminal":
                raise RuntimeError("aporia_prospective_censor_after_outcome")

            stmt = self.conn.prepare(
                """UPDATE aporia_ecological_relations_r3
                 SET enrollment_status = ?, censoring_reason = ?, censored_at = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE tenant_id = ? AND relation_ref = ? AND enrollment_status NOT IN (?, ?)"""
            )
            stmt.execute(["censored", reason_code, norm_censored_at, tenant_id, relation["relation_ref"], "censored", "terminal"])

            if started:
                self.conn.commit()
            return {"censored": True, "changed": stmt.row_count == 1}
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def data_gate(self, tenant_id: int) -> dict[str, Any]:
        stmt = self.conn.prepare(
            """SELECT relation.baseline_quartile, COUNT(*) AS terminal_relations
             FROM aporia_ecological_relations_r3 relation
             INNER JOIN aporia_ecological_predictions_r3 prediction
                     ON prediction.tenant_id = relation.tenant_id AND prediction.relation_ref = relation.relation_ref
             INNER JOIN aporia_ecological_terminal_outcomes_r3 outcome_row
                     ON outcome_row.tenant_id = relation.tenant_id AND outcome_row.relation_ref = relation.relation_ref
             WHERE relation.tenant_id = ? AND relation.provenance_complete = 1
               AND relation.enrollment_status = ?
               AND outcome_row.provenance_complete = 1
               AND prediction.prediction_sealed_at < outcome_row.outcome_timestamp
               AND prediction.runtime_influence = 0 AND prediction.external_effect = 0
               AND prediction.contamination_detected = 0
             GROUP BY relation.baseline_quartile"""
        )
        stmt.execute([tenant_id, "terminal"])
        quartiles = [0, 0, 0, 0]
        for row in stmt.fetch_all() or []:
            q = int(row["baseline_quartile"])
            if 0 <= q <= 3:
                quartiles[q] = int(row["terminal_relations"])

        total = sum(quartiles)
        passed = total >= 100 and min(quartiles) >= 20
        return {
            "total_terminal_relations": total,
            "quartiles": quartiles,
            "passed": passed,
            "generalization_g1_g6": "READY_FOR_FIXED_HORIZON_ANALYSIS" if passed else "BLOCKED_BY_REAL_DATA",
            "e_forecast": "READY_FOR_FIXED_HORIZON_ANALYSIS" if passed else "ACCRUING",
            "g8": "SEALED",
            "e_policy": "NOT_ELIGIBLE",
            "production": "NOT_ELIGIBLE",
            "efficacy_revealed": False,
        }

    def freeze_population_weights(self, tenant_id: int, version: str, weights: list[float], loss_margin: float) -> dict[str, Any]:
        if (
            not re.match(r"^[a-z0-9_.-]{3,64}$", version)
            or len(weights) != 4
            or loss_margin <= 0.0
            or loss_margin >= 1.0
        ):
            raise RuntimeError("aporia_ecological_weights_invalid")

        norm_weights = [float(w) for w in weights]
        if min(norm_weights) < 0.0 or abs(sum(norm_weights) - 1.0) > 1e-6:
            raise RuntimeError("aporia_ecological_weights_invalid")

        outcomes = self.conn.prepare("SELECT COUNT(*) FROM aporia_ecological_terminal_outcomes_r3 WHERE tenant_id = ?")
        outcomes.execute([tenant_id])
        if int(outcomes.fetch_column() or 0) > 0:
            raise RuntimeError("aporia_ecological_weights_after_outcome")

        encoded = json.dumps(norm_weights, separators=(",", ":"))
        margin = f"{loss_margin:.6f}"
        commitment = hashlib.sha256(f"{version}|{encoded}|{margin}".encode("utf-8")).hexdigest()

        stmt = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_ecological_population_weights_r3
                (tenant_id, weight_version, weights_json, loss_margin, design_commitment, frozen_at)
             VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)"""
        )
        stmt.execute([tenant_id, version, encoded, margin, commitment])
        if stmt.row_count == 1:
            return {"frozen": True, "new_record": True, "design_commitment": commitment}

        stored = self._design(tenant_id)
        if not hmac.compare_digest(str(stored.get("design_commitment", "")), commitment):
            raise RuntimeError("aporia_ecological_design_conflict")
        return {"frozen": True, "new_record": False, "design_commitment": commitment}

    def estimands(self, tenant_id: int) -> dict[str, Any]:
        gate = self.data_gate(tenant_id)
        if not gate["passed"]:
            raise RuntimeError("aporia_ecological_data_gate_blocked")
        design = self._design(tenant_id)
        if not design:
            raise RuntimeError("aporia_ecological_design_missing")

        weights = json.loads(str(design["weights_json"]))
        loss_margin = float(design["loss_margin"])
        rows = self._analysis_rows(tenant_id)
        effects = {}
        loss_differences = {}

        for quartile in range(4):
            cell = [r for r in rows if int(r["baseline_quartile"]) == quartile]
            c0 = [self._correct(str(r["c0_prediction_label"]), str(r["terminal_outcome"])) for r in cell]
            c5 = [self._correct(str(r["c5_prediction_label"]), str(r["terminal_outcome"])) for r in cell]

            effects[quartile] = sum(c5) / len(cell) - sum(c0) / len(cell)
            loss_differences[quartile] = (sum(1 - v for v in c5) / len(cell)) - (sum(1 - v for v in c0) / len(cell))

        balanced = sum(effects.values()) / 4.0
        population = sum(effects[q] * weights[q] for q in range(4))
        loss_diff = sum(loss_differences[q] * weights[q] for q in range(4))

        return {
            "balanced_effect": balanced,
            "population_effect": population,
            "quartile_effects": effects,
            "opportunity_loss_difference": loss_diff,
            "non_inferior_opportunity_loss": loss_diff < loss_margin,
            "loss_margin": loss_margin,
            "design_commitment": str(design["design_commitment"]),
            "forecast_metrics": {
                "C0": self._forecast_metrics(rows, "c0_win_probability"),
                "C5": self._forecast_metrics(rows, "c5_win_probability"),
            },
            "paired_by_relation": True,
            "e_policy": "NOT_ELIGIBLE",
        }

    sealPrediction = seal_prediction
    freezePopulationWeights = freeze_population_weights
    recordTerminal = record_terminal
    dataGate = data_gate

    def _baseline_quartile(self, tenant_id: int, relationship_score: int | None) -> int | None:
        if relationship_score is not None and 0 <= relationship_score <= 100:
            return min(3, relationship_score // 25)
        try:
            stmt = self.conn.prepare("SELECT corrected_self_score FROM aporia_obstruction_measurements WHERE tenant_id = ? ORDER BY id DESC LIMIT 1")
            stmt.execute([tenant_id])
            score = stmt.fetch_column()
            if score is None or score is False:
                return None
            return min(3, max(0, int(math.floor(float(score) * 4))))
        except Exception:
            return None

    def _relation(self, tenant_id: int, relationship_id: int, lock: bool = False) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT * FROM aporia_ecological_relations_r3 WHERE tenant_id = ? AND relationship_id = ? LIMIT 1")
        stmt.execute([tenant_id, relationship_id])
        return stmt.fetch() or {}

    def _prediction(self, tenant_id: int, relation_ref: str) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT * FROM aporia_ecological_predictions_r3 WHERE tenant_id = ? AND relation_ref = ? LIMIT 1")
        stmt.execute([tenant_id, relation_ref])
        return stmt.fetch() or {}

    def _terminal_outcome(self, tenant_id: int, relation_ref: str) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT * FROM aporia_ecological_terminal_outcomes_r3 WHERE tenant_id = ? AND relation_ref = ? LIMIT 1")
        stmt.execute([tenant_id, relation_ref])
        return stmt.fetch() or {}

    def _design(self, tenant_id: int, lock: bool = False) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT * FROM aporia_ecological_population_weights_r3 WHERE tenant_id = ? LIMIT 1")
        stmt.execute([tenant_id])
        return stmt.fetch() or {}

    def _analysis_rows(self, tenant_id: int) -> list[dict[str, Any]]:
        stmt = self.conn.prepare(
            """SELECT relation.baseline_quartile, prediction.c0_prediction_label,
                    prediction.c0_win_probability, prediction.c5_prediction_label,
                    prediction.c5_win_probability, outcome_row.terminal_outcome
             FROM aporia_ecological_relations_r3 relation
             INNER JOIN aporia_ecological_predictions_r3 prediction
                     ON prediction.tenant_id = relation.tenant_id AND prediction.relation_ref = relation.relation_ref
             INNER JOIN aporia_ecological_terminal_outcomes_r3 outcome_row
                     ON outcome_row.tenant_id = relation.tenant_id AND outcome_row.relation_ref = relation.relation_ref
             WHERE relation.tenant_id = ? AND relation.provenance_complete = 1
               AND relation.enrollment_status = ?
               AND outcome_row.provenance_complete = 1
               AND prediction.prediction_sealed_at < outcome_row.outcome_timestamp
               AND prediction.runtime_influence = 0 AND prediction.external_effect = 0
               AND prediction.contamination_detected = 0"""
        )
        stmt.execute([tenant_id, "terminal"])
        return stmt.fetch_all() or []

    def _forecast_metrics(self, rows: list[dict[str, Any]], prob_field: str) -> dict[str, Any]:
        probabilities = [float(r[prob_field]) for r in rows]
        outcomes = [1 if r["terminal_outcome"] == "won" else 0 for r in rows]
        count = len(rows)

        brier = 0.0
        log_loss = 0.0
        tp = 0
        fn = 0
        tn = 0
        fp = 0
        bins: list[list[tuple[float, int]]] = [[], [], []]

        for i, p in enumerate(probabilities):
            y = outcomes[i]
            brier += (p - y) ** 2
            bounded = min(1.0 - 1e-12, max(1e-12, p))
            log_loss -= y * math.log(bounded) + (1 - y) * math.log(1 - bounded)
            pos = p > 0.5
            if pos and y == 1:
                tp += 1
            elif not pos and y == 1:
                fn += 1
            elif not pos and y == 0:
                tn += 1
            elif pos and y == 0:
                fp += 1
            b_idx = 0 if p < 0.34 else (1 if p < 0.67 else 2)
            bins[b_idx].append((p, y))

        ece = 0.0
        for b in bins:
            if not b:
                continue
            m_p = sum(item[0] for item in b) / len(b)
            m_y = sum(item[1] for item in b) / len(b)
            ece += (len(b) / count) * abs(m_p - m_y)

        sens = tp / (tp + fn) if (tp + fn) > 0 else None
        spec = tn / (tn + fp) if (tn + fp) > 0 else None

        return {
            "brier_score": brier / count,
            "log_loss": log_loss / count,
            "expected_calibration_error": ece,
            "coverage": len(probabilities) / float(count),
            "sensitivity": sens,
            "specificity": spec,
            "threshold": 0.5,
        }

    def _correct(self, prediction: str, outcome: str) -> int:
        return int((prediction == "proceed" and outcome == "won") or (prediction in ("guard", "inspect") and outcome == "lost"))

    def _win_probability(self, prediction: str) -> float:
        if prediction == "proceed":
            return 0.75
        if prediction == "inspect":
            return 0.50
        if prediction == "guard":
            return 0.25
        raise RuntimeError("aporia_prospective_prediction_label_invalid")

    def _opaque(self, tenant_id: int, scope: str, value: str) -> str:
        return hmac.new(self.secret.encode("utf-8"), f"{tenant_id}|{scope}|{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _active(self, tenant_id: int) -> bool:
        return tenant_id == self.ACCOUNT_ID and self.environment.strip().lower() == "staging"
