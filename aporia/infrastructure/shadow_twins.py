"""Longitudinal shadow twins and shadow episode lifecycle ledger for Aporia."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any

from aporia.crypto import canonical_json
from aporia.infrastructure.db import Connection


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class AporiaShadowLifecycleLedger:
    ACCOUNT_ID = 49
    TERMINAL = ["turn_done_at", "usage_terminal_at", "outcome_terminal_at"]

    def __init__(self, conn: Connection, secret: str, environment: str):
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment

    def prepare(self, tenant_id: int, session_public_id: str, turn_id: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"prepared": False, "reason_codes": ["shadow_scope_inactive"]}
        self._validate(session_public_id, turn_id)
        turn_ref = self._turn_ref(tenant_id, turn_id)
        operation_id = hashlib.sha256(f"{tenant_id}|{turn_ref}|shadow-lifecycle".encode("utf-8")).hexdigest()
        timestamp = _now_str()
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            stmt = self.conn.prepare(
                """INSERT OR IGNORE INTO aporia_shadow_episode_lifecycle
                    (tenant_id, session_public_id, turn_id, turn_ref, operation_id, status,
                     assigned_at, preparing_at, prepared_at, runtime_influence, external_effect,
                     contamination_detected, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, ?, 0, 0, 0, ?, ?)"""
            )
            stmt.execute([
                tenant_id,
                session_public_id,
                turn_id,
                turn_ref,
                operation_id,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ])
            if stmt.row_count == 1:
                self._event(tenant_id, turn_ref, operation_id, "assigned", None, None, "new", "assigned", "worker", "system")
                self._event(tenant_id, turn_ref, operation_id, "preparing", None, None, "assigned", "preparing", "worker", "system")
                self._event(tenant_id, turn_ref, operation_id, "prepared", None, None, "preparing", "prepared", "worker", "system")
            else:
                self._assert_existing_scope(tenant_id, turn_ref, session_public_id, turn_id)
            if started:
                self.conn.commit()
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise
        return {"prepared": True, "turn_ref": turn_ref, "operation_id": operation_id}

    def expose_and_seal_predictions(self, tenant_id: int, turn_id: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"sealed": False, "reason_codes": ["shadow_scope_inactive"]}
        self._transition(tenant_id, turn_id, "exposed")
        self._transition(tenant_id, turn_id, "prediction_sealed", "C0")
        self._transition(tenant_id, turn_id, "prediction_sealed", "C5")
        return {"sealed": True, "arms": ["C0", "C5"]}

    def mark_turn_and_usage_terminal(self, tenant_id: int, turn_id: str) -> None:
        self.mark_turn_terminal(tenant_id, turn_id)
        self.mark_usage_terminal(tenant_id, turn_id)

    def mark_turn_terminal(self, tenant_id: int, turn_id: str) -> None:
        if not self._active(tenant_id):
            return
        self._transition(tenant_id, turn_id, "turn_done")
        self._revoke_turn_if_ready(tenant_id, turn_id)

    def mark_usage_terminal(self, tenant_id: int, turn_id: str) -> None:
        if not self._active(tenant_id):
            return
        self._transition(tenant_id, turn_id, "usage_terminal")
        self._revoke_turn_if_ready(tenant_id, turn_id)

    def mark_outcome_terminal(self, tenant_id: int, turn_id: str, outcome_commitment: str) -> None:
        if not self._active(tenant_id):
            return
        if not re.match(r"^[0-9a-f]{64}$", outcome_commitment):
            raise RuntimeError("aporia_shadow_lifecycle_outcome_invalid")
        self._transition(tenant_id, turn_id, "outcome_terminal", None, outcome_commitment)
        self._revoke_turn_if_ready(tenant_id, turn_id)

    def mark_timed_out(self, tenant_id: int, turn_id: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"timed_out": False, "reason_codes": ["shadow_scope_inactive"]}
        result = self._transition(tenant_id, turn_id, "timeout")
        self._revoke_turn_if_ready(tenant_id, turn_id)
        return result

    def assert_session_can_revoke(self, tenant_id: int, session_public_id: str) -> None:
        if not self._active(tenant_id):
            return
        stmt = self.conn.prepare(
            """SELECT turn_id, turn_done_at, usage_terminal_at, outcome_terminal_at, timed_out_at, missing_json
             FROM aporia_shadow_episode_lifecycle
             WHERE tenant_id = ? AND session_public_id = ? AND revoked_at IS NULL"""
        )
        stmt.execute([tenant_id, session_public_id])
        for row in stmt.fetch_all() or []:
            if not self._terminal_barrier_satisfied(row):
                turn_ref = self._turn_ref(tenant_id, str(row["turn_id"]))
                stored = self._row(tenant_id, turn_ref, False)
                if stored:
                    self._event(
                        tenant_id,
                        turn_ref,
                        str(stored["operation_id"]),
                        "revoke_rejected",
                        None,
                        None,
                        str(stored["status"]),
                        str(stored["status"]),
                        "worker",
                        "system",
                        "rejected",
                        "denied",
                        "terminal_barrier_pending",
                    )
                raise RuntimeError("aporia_shadow_lifecycle_finalization_pending")

    def archive_session(self, tenant_id: int, session_public_id: str) -> dict[str, int]:
        if not self._active(tenant_id):
            return {"archived": 0, "revoked": 0, "pending": 0}
        stmt = self.conn.prepare(
            "SELECT turn_id FROM aporia_shadow_episode_lifecycle WHERE tenant_id = ? AND session_public_id = ? AND revoked_at IS NULL"
        )
        stmt.execute([tenant_id, session_public_id])
        archived = 0
        revoked = 0
        pending = 0
        for row in stmt.fetch_all() or []:
            turn_id = str(row["turn_id"])
            archive = self._transition(tenant_id, turn_id, "archived", None, None, "user", "user")
            archived += int(bool(archive.get("recorded")))
            if self._revoke_turn_if_ready(tenant_id, turn_id):
                revoked += 1
            else:
                pending += 1
        return {"archived": archived, "revoked": revoked, "pending": pending}

    def reconcile_archived_before(self, tenant_id: int, cutoff: str, limit: int = 100) -> dict[str, int]:
        if not self._active(tenant_id):
            return {"timed_out": 0, "revoked": 0}
        if limit < 1 or limit > 1000 or not cutoff:
            raise RuntimeError("aporia_shadow_lifecycle_reconcile_invalid")

        stmt = self.conn.prepare(
            """SELECT turn_id FROM aporia_shadow_episode_lifecycle
             WHERE tenant_id = ? AND archived_at IS NOT NULL AND revoked_at IS NULL
               AND timed_out_at IS NULL AND updated_at <= ?
             ORDER BY updated_at ASC LIMIT ?"""
        )
        stmt.execute([tenant_id, cutoff, limit])
        timed_out = 0
        revoked = 0
        for row in stmt.fetch_all() or []:
            turn_id = str(row["turn_id"])
            if self._revoke_turn_if_ready(tenant_id, turn_id):
                revoked += 1
                continue
            res = self.mark_timed_out(tenant_id, turn_id)
            timed_out += int(bool(res.get("recorded")))
            turn_ref = self._turn_ref(tenant_id, turn_id)
            r = self._row(tenant_id, turn_ref, False)
            revoked += int(bool(r and r.get("revoked_at") is not None))
        return {"timed_out": timed_out, "revoked": revoked}

    exposeAndSealPredictions = expose_and_seal_predictions
    markTurnAndUsageTerminal = mark_turn_and_usage_terminal
    markTurnTerminal = mark_turn_terminal
    markUsageTerminal = mark_usage_terminal
    markOutcomeTerminal = mark_outcome_terminal
    markTimedOut = mark_timed_out
    assertSessionCanRevoke = assert_session_can_revoke
    archiveSession = archive_session
    reconcileArchivedBefore = reconcile_archived_before

    def _transition(
        self,
        tenant_id: int,
        turn_id: str,
        transition: str,
        arm: str | None = None,
        payload_commitment: str | None = None,
        actor_type: str = "worker",
        stream: str = "system",
    ) -> dict[str, Any]:
        allowed = ["exposed", "prediction_sealed", "turn_done", "usage_terminal", "outcome_terminal", "timeout", "archived", "revoked"]
        if transition not in allowed or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id):
            raise RuntimeError("aporia_shadow_lifecycle_transition_invalid")
        if transition == "prediction_sealed" and arm not in ("C0", "C5"):
            raise RuntimeError("aporia_shadow_lifecycle_arm_invalid")

        turn_ref = self._turn_ref(tenant_id, turn_id)
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            row = self._row(tenant_id, turn_ref, True)
            if not row:
                raise RuntimeError("aporia_shadow_lifecycle_unavailable")
            field = self._field(transition, arm)
            if row.get("revoked_at") is not None and transition != "revoked":
                raise RuntimeError("aporia_shadow_lifecycle_revoked")
            if (
                row.get("timed_out_at") is not None
                and transition in ("turn_done", "usage_terminal", "outcome_terminal")
                and row.get(field) is None
            ):
                raise RuntimeError("aporia_shadow_lifecycle_missing_is_terminal")
            if (
                transition == "outcome_terminal"
                and row.get("outcome_commitment") is not None
                and not hmac.compare_digest(str(row["outcome_commitment"]), str(payload_commitment))
            ):
                raise RuntimeError("aporia_shadow_lifecycle_outcome_conflict")
            if row.get(field) is not None:
                if started:
                    self.conn.commit()
                return {"recorded": False, "deduplicated": True, "status": str(row["status"])}

            self._assert_prerequisites(row, transition, arm)
            timestamp = _now_str()
            before = str(row["status"])
            sets = [f"{field} = ?", "updated_at = ?"]
            params = [timestamp, timestamp]

            if transition == "outcome_terminal":
                sets.append("outcome_commitment = ?")
                params.append(payload_commitment)
            if transition == "timeout":
                missing = self._missing(row)
                sets.append("missing_json = ?")
                params.append(json.dumps(missing, separators=(",", ":")))

            params.extend([tenant_id, turn_ref])
            upd_sql = f"UPDATE aporia_shadow_episode_lifecycle SET {', '.join(sets)} WHERE tenant_id = ? AND turn_ref = ?"
            upd_stmt = self.conn.prepare(upd_sql)
            upd_stmt.execute(params)

            updated = self._row(tenant_id, turn_ref, False)
            after = self._derived_status(updated)
            status_stmt = self.conn.prepare(
                "UPDATE aporia_shadow_episode_lifecycle SET status = ?, updated_at = ? WHERE tenant_id = ? AND turn_ref = ?"
            )
            status_stmt.execute([after, timestamp, tenant_id, turn_ref])

            self._event(
                tenant_id,
                turn_ref,
                str(row["operation_id"]),
                transition,
                arm,
                payload_commitment,
                before,
                after,
                actor_type,
                stream,
            )
            if started:
                self.conn.commit()
            return {"recorded": True, "deduplicated": False, "status": after}
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def _assert_prerequisites(self, row: dict[str, Any], transition: str, arm: str | None) -> None:
        if transition == "exposed" and row.get("prepared_at") is None:
            raise RuntimeError("aporia_shadow_lifecycle_not_prepared")
        if transition == "prediction_sealed" and row.get("exposed_at") is None:
            raise RuntimeError("aporia_shadow_lifecycle_not_exposed")
        if (
            transition in ("turn_done", "usage_terminal", "outcome_terminal")
            and (row.get("c0_prediction_sealed_at") is None or row.get("c5_prediction_sealed_at") is None)
        ):
            raise RuntimeError("aporia_shadow_lifecycle_predictions_incomplete")
        if transition == "revoked" and not self._terminal_barrier_satisfied(row):
            raise RuntimeError("aporia_shadow_lifecycle_revocation_barrier_not_satisfied")
        if transition == "revoked" and row.get("archived_at") is None and row.get("timed_out_at") is None:
            raise RuntimeError("aporia_shadow_lifecycle_not_archived")

    def _terminal_barrier_satisfied(self, row: dict[str, Any]) -> bool:
        complete = all(row.get(f) is not None for f in self.TERMINAL)
        if complete:
            return True
        if row.get("timed_out_at") is None or not isinstance(row.get("missing_json"), str):
            return False
        missing = json.loads(row["missing_json"])
        missing.sort()
        expected = self._missing(row)
        expected.sort()
        return missing == expected and len(missing) > 0

    def _missing(self, row: dict[str, Any]) -> list[str]:
        missing = []
        for field in self.TERMINAL:
            if row.get(field) is None:
                missing.append(field[:-3])
        return missing

    def _derived_status(self, row: dict[str, Any]) -> str:
        order = [
            ("revoked_at", "revoked"),
            ("archived_at", "archived"),
            ("outcome_terminal_at", "outcome_terminal"),
            ("usage_terminal_at", "usage_terminal"),
            ("turn_done_at", "turn_done"),
            ("c5_prediction_sealed_at", "prediction_sealed"),
            ("exposed_at", "exposed"),
            ("prepared_at", "prepared"),
            ("preparing_at", "preparing"),
            ("assigned_at", "assigned"),
        ]
        for field, status in order:
            if row.get(field) is not None:
                return status
        return "new"

    def _event(
        self,
        tenant_id: int,
        turn_ref: str,
        operation_id: str,
        transition: str,
        arm: str | None,
        payload_commitment: str | None,
        before: str,
        after: str,
        actor_type: str,
        stream: str,
        lifecycle_phase: str = "succeeded",
        outcome: str = "succeeded",
        reason_code: str = "state_transition_recorded",
    ) -> None:
        event_key = f"{operation_id}|{transition}|{arm or ''}"
        event_id = self._uuid(hashlib.sha256(event_key.encode("utf-8")).hexdigest())
        stmt = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_shadow_lifecycle_events
                (event_id, tenant_id, turn_ref, transition_name, arm, payload_commitment,
                 event_name, event_version, environment, stream, category, component, operation_id,
                 actor_type, target_type, action_name, lifecycle_phase, outcome, reason_code,
                 state_before, state_after, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'audit', 'aporia-shadow-lifecycle', ?, ?,
                     'shadow_episode', ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            event_id,
            tenant_id,
            turn_ref,
            transition,
            arm,
            payload_commitment,
            f"aporia.shadow.lifecycle.{transition}",
            self.environment[:24],
            stream,
            operation_id,
            actor_type,
            transition,
            lifecycle_phase,
            outcome,
            reason_code,
            before,
            after,
            _now_str(),
        ])

    def _revoke_turn_if_ready(self, tenant_id: int, turn_id: str) -> bool:
        if not self._active(tenant_id):
            return False
        turn_ref = self._turn_ref(tenant_id, turn_id)
        row = self._row(tenant_id, turn_ref, False)
        if not row or row.get("revoked_at") is not None or row.get("archived_at") is None or not self._terminal_barrier_satisfied(row):
            return False
        self._transition(tenant_id, turn_id, "revoked", None, None, "worker", "system")
        return True

    def _row(self, tenant_id: int, turn_ref: str, lock: bool) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT * FROM aporia_shadow_episode_lifecycle WHERE tenant_id = ? AND turn_ref = ? LIMIT 1")
        stmt.execute([tenant_id, turn_ref])
        return stmt.fetch() or {}

    def _assert_existing_scope(self, tenant_id: int, turn_ref: str, session_public_id: str, turn_id: str) -> None:
        row = self._row(tenant_id, turn_ref, False)
        if (
            not row
            or not hmac.compare_digest(str(row.get("session_public_id")), session_public_id)
            or not hmac.compare_digest(str(row.get("turn_id")), turn_id)
        ):
            raise RuntimeError("aporia_shadow_lifecycle_scope_conflict")

    def _field(self, transition: str, arm: str | None) -> str:
        if transition == "prediction_sealed":
            return f"{str(arm).lower()}_prediction_sealed_at"
        mapping = {
            "exposed": "exposed_at",
            "turn_done": "turn_done_at",
            "usage_terminal": "usage_terminal_at",
            "outcome_terminal": "outcome_terminal_at",
            "timeout": "timed_out_at",
            "archived": "archived_at",
            "revoked": "revoked_at",
        }
        if transition not in mapping:
            raise RuntimeError("aporia_shadow_lifecycle_transition_invalid")
        return mapping[transition]

    def _validate(self, session_public_id: str, turn_id: str) -> None:
        if (
            not re.match(r"^[0-9a-fA-F-]{36}$", session_public_id)
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id)
            or not self.secret
        ):
            raise RuntimeError("aporia_shadow_lifecycle_invalid")

    def _turn_ref(self, tenant_id: int, turn_id: str) -> str:
        if not self.secret:
            raise RuntimeError("aporia_shadow_lifecycle_secret_missing")
        msg = f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}"
        return hmac.new(self.secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

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


class AporiaLongitudinalShadowTwins:
    ACCOUNT_ID = 49
    TABLES = {
        "C0": "aporia_longitudinal_shadow_c0",
        "C5": "aporia_longitudinal_shadow_c5",
    }

    def __init__(self, conn: Connection, secret: str, environment: str):
        self.conn = conn
        self.secret = secret.strip()
        self.environment = environment

    def capture(self, tenant_id: int, turn_id: str, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"captured": False, "reason_codes": ["shadow_scope_inactive"]}
        if not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id) or not self.secret:
            raise RuntimeError("aporia_longitudinal_shadow_invalid")

        turn_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        obs_json = self._canonical_json(observation)
        obs_commitment = hmac.new(self.secret.encode("utf-8"), obs_json.encode("utf-8"), hashlib.sha256).hexdigest()

        c5_pred = self._c5_prediction(tenant_id)
        created_at = _now_str()

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            for arm, prediction in [("C0", "proceed"), ("C5", c5_pred)]:
                pred_msg = f"{arm}|{turn_ref}|{obs_commitment}|{prediction}"
                pred_commitment = hmac.new(self.secret.encode("utf-8"), pred_msg.encode("utf-8"), hashlib.sha256).hexdigest()
                op_id = hashlib.sha256(f"{tenant_id}|{turn_ref}|{arm}|capture".encode("utf-8")).hexdigest()
                shadow_id = self._uuid(hashlib.sha256(f"{op_id}|shadow".encode("utf-8")).hexdigest())

                self._insert_ignore(
                    self.TABLES[arm],
                    [
                        shadow_id,
                        tenant_id,
                        turn_ref,
                        obs_commitment,
                        prediction,
                        pred_commitment,
                        self.environment[:24],
                        op_id,
                        created_at,
                        created_at,
                    ],
                )
            if started:
                self.conn.commit()
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

        return {
            "captured": True,
            "turn_ref": turn_ref,
            "same_event": True,
            "separate_stores": True,
            "predictions_before_outcome": True,
        }

    def record_outcome(self, tenant_id: int, turn_id: str, outcome_commitment: str) -> dict[str, Any]:
        if not self._active(tenant_id):
            return {"recorded": False, "reason_codes": ["shadow_scope_inactive"]}
        if (
            not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id)
            or not re.match(r"^[0-9a-f]{64}$", outcome_commitment)
            or not self.secret
        ):
            raise RuntimeError("aporia_longitudinal_shadow_outcome_invalid")

        turn_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"aporia-longitudinal-shadow|{tenant_id}|{turn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        updated_at = _now_str()
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            for table in self.TABLES.values():
                stmt = self.conn.prepare(
                    f"""UPDATE {table}
                     SET outcome_commitment = ?, outcome_recorded_at = ?, updated_at = ?
                     WHERE tenant_id = ? AND turn_ref = ? AND outcome_commitment IS NULL
                       AND assigned_before_outcome = 1 AND runtime_influence = 0 AND external_effect = 0"""
                )
                stmt.execute([outcome_commitment, updated_at, updated_at, tenant_id, turn_ref])
                if stmt.row_count != 1:
                    check_stmt = self.conn.prepare(f"SELECT outcome_commitment FROM {table} WHERE tenant_id = ? AND turn_ref = ? LIMIT 1")
                    check_stmt.execute([tenant_id, turn_ref])
                    stored = check_stmt.fetch_column()
                    if not stored or not hmac.compare_digest(str(stored), outcome_commitment):
                        raise RuntimeError("aporia_longitudinal_shadow_pair_incomplete")
            if started:
                self.conn.commit()
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

        return {"recorded": True, "paired_arms": ["C0", "C5"], "runtime_influence": False}

    recordOutcome = record_outcome

    def _active(self, tenant_id: int) -> bool:
        return tenant_id == self.ACCOUNT_ID and self.environment.strip().lower() == "staging"

    def _c5_prediction(self, tenant_id: int) -> str:
        stmt = self.conn.prepare(
            "SELECT corrected_self_score, cycle_score FROM aporia_obstruction_measurements WHERE tenant_id = ? ORDER BY id DESC LIMIT 1"
        )
        stmt.execute([tenant_id])
        row = stmt.fetch()
        if not row:
            return "inspect"
        return "guard" if max(float(row["corrected_self_score"]), float(row["cycle_score"])) >= 0.5 else "proceed"

    def _insert_ignore(self, table: str, parameters: list[Any]) -> None:
        stmt = self.conn.prepare(
            f"""INSERT OR IGNORE INTO {table}
                (shadow_id, tenant_id, turn_ref, observation_commitment, prediction_label,
                 prediction_commitment, assigned_before_outcome, runtime_influence, external_effect,
                 contamination_detected, event_name, event_version, environment, stream, category,
                 component, operation_id, actor_type, action_name, lifecycle_phase, outcome,
                 reason_code, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, 0, 'aporia.longitudinal.shadow.prediction.recorded',
                     1, ?, 'system', 'audit', 'aporia-longitudinal-shadow', ?, 'worker',
                     'record_shadow_prediction', 'succeeded', 'succeeded',
                     'prediction_before_outcome', ?, ?)"""
        )
        stmt.execute(parameters)

    def _canonical_json(self, value: Any) -> str:
        def normalise(item: Any) -> Any:
            if isinstance(item, dict):
                return {k: normalise(v) for k, v in sorted(item.items())}
            if isinstance(item, list):
                return [normalise(x) for x in item]
            return item
        return json.dumps(normalise(value), separators=(",", ":"), ensure_ascii=False)

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


class AporiaLongitudinalShadowTwinsR1:
    def __init__(self, conn: Connection, secret: str, environment: str):
        self.conn = conn
        self.twins = AporiaLongitudinalShadowTwins(conn, secret, environment)
        self.lifecycle = AporiaShadowLifecycleLedger(conn, secret, environment)

    def capture(self, tenant_id: int, turn_id: str, observation: dict[str, Any], session_public_id: str) -> dict[str, Any]:
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            self.lifecycle.prepare(tenant_id, session_public_id, turn_id)
            result = self.twins.capture(tenant_id, turn_id, observation)
            self.lifecycle.expose_and_seal_predictions(tenant_id, turn_id)
            if started:
                self.conn.commit()
            return result
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def record_outcome(self, tenant_id: int, turn_id: str, outcome_commitment: str) -> dict[str, Any]:
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()
        try:
            result = self.twins.record_outcome(tenant_id, turn_id, outcome_commitment)
            self.lifecycle.mark_outcome_terminal(tenant_id, turn_id, outcome_commitment)
            if started:
                self.conn.commit()
            return result
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    recordOutcome = record_outcome

