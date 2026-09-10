"""
Aporia Guarded Policy persistence and enforcement.
"""

from __future__ import annotations
from datetime import datetime, timezone
import re
from typing import Any, Mapping
from aporia.crypto import sha256_hex, hash_equals
from aporia.infrastructure.db import Connection


class AporiaGuardedPolicy:
    def __init__(self, conn: Connection | Any, environment: str = "unknown") -> None:
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.environment = environment

    def approve(
        self,
        tenant_id: int,
        approver_user_id: int,
        approval_commitment: str,
        risk_threshold: float,
        daily_call_limit: int,
        daily_cost_limit: float,
    ) -> dict[str, Any]:
        if (
            tenant_id < 1
            or approver_user_id < 1
            or not re.match(r"^[0-9a-f]{64}$", approval_commitment)
            or risk_threshold < 0.0
            or risk_threshold > 1.0
            or daily_call_limit < 1
            or daily_cost_limit <= 0.0
        ):
            raise ValueError("aporia_guarded_approval_invalid")

        return self._change(
            tenant_id,
            approver_user_id,
            approval_commitment,
            "approved",
            True,
            risk_threshold,
            daily_call_limit,
            daily_cost_limit,
        )

    def activate(self, tenant_id: int, approver_user_id: int, approval_commitment: str) -> dict[str, Any]:
        return self._change(tenant_id, approver_user_id, approval_commitment, "active", False, None, None, None)

    def rollback(self, tenant_id: int, approver_user_id: int, approval_commitment: str) -> dict[str, Any]:
        return self._change(tenant_id, approver_user_id, approval_commitment, "rolled_back", True, None, None, None)

    def stop_for_critical_regression(self, tenant_id: int) -> bool:
        if tenant_id < 1:
            raise ValueError("aporia_guarded_policy_invalid")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare("SELECT * FROM aporia_guarded_policies WHERE tenant_id = ? LIMIT 1")
            stmt.execute([tenant_id])
            current = stmt.fetch()
            if not current or str(current.get("status", "")) != "active" or int(current.get("kill_switch", 0)) != 0:
                if started:
                    self.conn.commit()
                return False

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            update = self.conn.prepare("UPDATE aporia_guarded_policies SET status = 'rolled_back', kill_switch = 1, updated_at = ? WHERE tenant_id = ?")
            update.execute([now, tenant_id])
            self._record_automatic_stop_event(tenant_id, current)

            if started:
                self.conn.commit()
            return True
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def stopForCriticalRegression(self, tenant_id: int) -> bool:
        return self.stop_for_critical_regression(tenant_id)

    def _change(
        self,
        tenant_id: int,
        approver_user_id: int,
        approval_commitment: str,
        target: str,
        kill_switch: bool,
        risk_threshold: float | None,
        daily_call_limit: int | None,
        daily_cost_limit: float | None,
    ) -> dict[str, Any]:
        if tenant_id < 1 or approver_user_id < 1 or not re.match(r"^[0-9a-f]{64}$", approval_commitment):
            raise ValueError("aporia_guarded_policy_invalid")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare("SELECT * FROM aporia_guarded_policies WHERE tenant_id = ? LIMIT 1")
            stmt.execute([tenant_id])
            current = stmt.fetch()

            if target == "approved":
                if current and str(current.get("status", "")) not in ("disabled", "rolled_back"):
                    raise ValueError("aporia_guarded_policy_transition_invalid")
                self._upsert_approval(tenant_id, approver_user_id, approval_commitment, risk_threshold, daily_call_limit, daily_cost_limit)
            else:
                if (
                    not current
                    or not hash_equals(str(current.get("approval_commitment", "")), approval_commitment)
                    or int(current.get("approved_by_user_id", 0)) != approver_user_id
                ):
                    raise ValueError("aporia_guarded_policy_transition_invalid")
                if target == "active" and str(current.get("status", "")) != "approved":
                    raise ValueError("aporia_guarded_policy_transition_invalid")
                if target == "rolled_back" and str(current.get("status", "")) not in ("approved", "active"):
                    raise ValueError("aporia_guarded_policy_transition_invalid")

                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
                update = self.conn.prepare("UPDATE aporia_guarded_policies SET status = ?, kill_switch = ?, updated_at = ? WHERE tenant_id = ?")
                update.execute([target, 1 if kill_switch else 0, now, tenant_id])

            self._record_event(tenant_id, approver_user_id, approval_commitment, target)
            if started:
                self.conn.commit()
            return {"status": target, "kill_switch": kill_switch}
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def _upsert_approval(
        self,
        tenant_id: int,
        approver_user_id: int,
        approval_commitment: str,
        risk_threshold: float | None,
        daily_call_limit: int | None,
        daily_cost_limit: float | None,
    ) -> None:
        sql = (
            "INSERT INTO aporia_guarded_policies "
            "(tenant_id, status, approval_commitment, approved_by_user_id, risk_threshold, daily_call_limit, daily_cost_limit, kill_switch) "
            "VALUES (?, 'approved', ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(tenant_id) DO UPDATE SET status = 'approved', approval_commitment = excluded.approval_commitment, "
            "   approved_by_user_id = excluded.approved_by_user_id, risk_threshold = excluded.risk_threshold, "
            "   daily_call_limit = excluded.daily_call_limit, daily_cost_limit = excluded.daily_cost_limit, kill_switch = 1"
        )
        self.conn.prepare(sql).execute([tenant_id, approval_commitment, approver_user_id, risk_threshold, daily_call_limit, daily_cost_limit])

    def _record_event(self, tenant_id: int, approver_user_id: int, approval_commitment: str, status: str) -> None:
        action = "approve_guarded_policy" if status == "approved" else ("activate_guarded_policy" if status == "active" else "rollback_guarded_policy")
        operation_id = sha256_hex(f"{tenant_id}|{approval_commitment}|{status}")
        event_id = self._uuid(sha256_hex(f"{operation_id}|event"))
        # Ensure event table exists
        self.conn.exec(
            "CREATE TABLE IF NOT EXISTS aporia_guarded_policy_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, policy_event_id TEXT NOT NULL UNIQUE, "
            "tenant_id INTEGER NOT NULL, status TEXT NOT NULL, approval_commitment TEXT NOT NULL, "
            "approved_by_user_id INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, "
            "environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, "
            "operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, "
            "lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        sql = (
            "INSERT OR IGNORE INTO aporia_guarded_policy_events "
            "(policy_event_id, tenant_id, status, approval_commitment, approved_by_user_id, "
            " event_name, event_version, environment, stream, category, component, operation_id, "
            " actor_type, action_name, lifecycle_phase, outcome, reason_code) "
            "VALUES (?, ?, ?, ?, ?, 'aporia.guarded.policy.changed', 1, ?, 'system', 'audit', "
            "        'aporia-guarded-policy', ?, 'user', ?, 'succeeded', 'succeeded', ?)"
        )
        self.conn.prepare(sql).execute([
            event_id,
            tenant_id,
            status,
            approval_commitment,
            approver_user_id,
            self.environment[:24],
            operation_id,
            action,
            "policy_" + status,
        ])

    def _record_automatic_stop_event(self, tenant_id: int, policy: Mapping[str, Any]) -> None:
        approval_commitment = str(policy.get("approval_commitment", ""))
        operation_id = sha256_hex(f"{tenant_id}|{approval_commitment}|{policy.get('updated_at', '')}|critical-regression-auto-stop")
        event_id = self._uuid(sha256_hex(f"{operation_id}|event"))
        self.conn.exec(
            "CREATE TABLE IF NOT EXISTS aporia_guarded_policy_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, policy_event_id TEXT NOT NULL UNIQUE, "
            "tenant_id INTEGER NOT NULL, status TEXT NOT NULL, approval_commitment TEXT NOT NULL, "
            "approved_by_user_id INTEGER NOT NULL, event_name TEXT NOT NULL, event_version INTEGER NOT NULL, "
            "environment TEXT NOT NULL, stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, "
            "operation_id TEXT NOT NULL, actor_type TEXT NOT NULL, action_name TEXT NOT NULL, "
            "lifecycle_phase TEXT NOT NULL, outcome TEXT NOT NULL, reason_code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        sql = (
            "INSERT OR IGNORE INTO aporia_guarded_policy_events "
            "(policy_event_id, tenant_id, status, approval_commitment, approved_by_user_id, "
            " event_name, event_version, environment, stream, category, component, operation_id, "
            " actor_type, action_name, lifecycle_phase, outcome, reason_code) "
            "VALUES (?, ?, 'rolled_back', ?, ?, 'aporia.guarded.policy.changed', 1, ?, 'system', 'audit', "
            "        'aporia-guarded-policy', ?, 'system', 'auto_stop_guarded_policy', 'succeeded', 'succeeded', "
            "        'critical_regression_observed')"
        )
        self.conn.prepare(sql).execute([
            event_id,
            tenant_id,
            approval_commitment,
            int(policy.get("approved_by_user_id", 0)),
            self.environment[:24],
            operation_id,
        ])

    @staticmethod
    def _uuid(hash_str: str) -> str:
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
