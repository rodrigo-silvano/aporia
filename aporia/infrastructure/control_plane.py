"""
Aporia Independent Control Plane.
Provides immediate, independent kill-switches for global, tenant, runtime influence,
memory writes, ontology, and HIRT advisory planes.
"""

from __future__ import annotations
import re
from typing import Any, Sequence
from aporia.config import Environment
from aporia.crypto import sha256_hex
from aporia.infrastructure.db import Connection


class AporiaIndependentControlPlane:
    GLOBAL = "global"
    TENANT = "tenant"
    RUNTIME_INFLUENCE = "runtime_influence"
    MEMORY_WRITES = "memory_writes"
    ONTOLOGY = "ontology"
    HIRT_ADVISORY = "hirt_advisory"

    SWITCHES = (
        GLOBAL,
        TENANT,
        RUNTIME_INFLUENCE,
        MEMORY_WRITES,
        ONTOLOGY,
        HIRT_ADVISORY,
    )

    ENVIRONMENT_SWITCHES = {
        GLOBAL: "APORIA_KILL_GLOBAL",
        RUNTIME_INFLUENCE: "APORIA_KILL_RUNTIME_INFLUENCE",
        MEMORY_WRITES: "APORIA_KILL_MEMORY_WRITES",
        ONTOLOGY: "APORIA_KILL_ONTOLOGY",
        HIRT_ADVISORY: "APORIA_KILL_HIRT_ADVISORY",
    }

    def __init__(self, conn: Connection | Any, environment: Environment | None = None) -> None:
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.environment = environment or Environment()

    def engaged(self, tenant_id: int, switch: str) -> bool:
        self._validate(tenant_id, switch)
        if self._environment_engaged(tenant_id, switch):
            return True

        try:
            stmt = self.conn.prepare(
                "SELECT engaged FROM aporia_independent_control_switches "
                "WHERE switch_name = ? AND tenant_id IN (0, ?) ORDER BY tenant_id DESC"
            )
            stmt.execute([switch, tenant_id])
            for row in stmt.fetchAll():
                if int(row["engaged"]) == 1:
                    return True
        except Exception:
            # Fails closed on database unavailability
            return True

        return False

    def any_engaged(self, tenant_id: int, switches: Sequence[str]) -> bool:
        for sw in switches:
            if self.engaged(tenant_id, str(sw)):
                return True
        return False

    def anyEngaged(self, tenant_id: int, switches: Sequence[str]) -> bool:
        return self.any_engaged(tenant_id, switches)

    def snapshot(self, tenant_id: int) -> dict[str, bool]:
        if tenant_id < 1:
            raise ValueError("aporia_control_switch_invalid")
        return {
            self.GLOBAL: self.engaged(tenant_id, self.GLOBAL),
            self.TENANT: self.engaged(tenant_id, self.TENANT),
            self.RUNTIME_INFLUENCE: self.engaged(tenant_id, self.RUNTIME_INFLUENCE),
            self.MEMORY_WRITES: self.engaged(tenant_id, self.MEMORY_WRITES),
            self.ONTOLOGY: self.engaged(tenant_id, self.ONTOLOGY),
            self.HIRT_ADVISORY: self.engaged(tenant_id, self.HIRT_ADVISORY),
        }

    def set(
        self,
        tenant_id: int,
        switch: str,
        engaged: bool,
        operator_user_id: int,
        reason_code: str,
    ) -> dict[str, Any]:
        self._validate(tenant_id, switch)
        if switch == self.GLOBAL and tenant_id != 0:
            raise ValueError("aporia_control_switch_invalid")
        if operator_user_id < 1 or not re.match(r"^[a-z0-9_]{3,64}$", reason_code):
            raise ValueError("aporia_control_switch_operator_invalid")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare(
                "SELECT engaged, revision FROM aporia_independent_control_switches "
                "WHERE tenant_id = ? AND switch_name = ? LIMIT 1"
            )
            stmt.execute([tenant_id, switch])
            current = stmt.fetch()
            before = int(current["engaged"]) if current else 0
            revision = int(current["revision"]) if current else 0

            changed = (before != int(engaged)) or (current is None)
            if changed:
                new_rev = revision + 1
                self._persist(tenant_id, switch, engaged, operator_user_id, reason_code, new_rev)
                self._audit(tenant_id, switch, before, int(engaged), operator_user_id, reason_code, new_rev)
                revision = new_rev

            if started:
                self.conn.commit()

            return {
                "tenant_id": tenant_id,
                "switch": switch,
                "engaged": engaged,
                "changed": changed,
                "revision": revision,
            }
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def _environment_engaged(self, tenant_id: int, switch: str) -> bool:
        if switch == self.TENANT:
            raw_ids = self.environment.get_string("APORIA_KILL_TENANT_IDS", "")
            ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
            return str(tenant_id) in ids

        key = self.ENVIRONMENT_SWITCHES.get(switch)
        return bool(key and self.environment.get_bool(key, False))

    def _persist(
        self,
        tenant_id: int,
        switch: str,
        engaged: bool,
        operator_user_id: int,
        reason_code: str,
        revision: int,
    ) -> None:
        stmt = self.conn.prepare(
            "INSERT INTO aporia_independent_control_switches "
            "(tenant_id, switch_name, engaged, revision, changed_by_user_id, reason_code, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(tenant_id, switch_name) DO UPDATE SET "
            "   engaged = excluded.engaged, revision = excluded.revision, "
            "   changed_by_user_id = excluded.changed_by_user_id, reason_code = excluded.reason_code, "
            "   updated_at = CURRENT_TIMESTAMP"
        )
        stmt.execute([tenant_id, switch, 1 if engaged else 0, revision, operator_user_id, reason_code])

    def _audit(
        self,
        tenant_id: int,
        switch: str,
        before: int,
        after: int,
        operator_user_id: int,
        reason_code: str,
        revision: int,
    ) -> None:
        op_input = f"{tenant_id}|{switch}|{revision}|{before}|{after}"
        operation_id = sha256_hex(op_input)
        event_id = self._uuid(operation_id)
        stmt = self.conn.prepare(
            "INSERT INTO aporia_independent_control_events "
            "(event_id, tenant_id, switch_name, state_before, state_after, revision, "
            " event_name, event_version, environment, stream, category, component, "
            " operation_id, actor_type, actor_user_id, target_type, action_name, "
            " lifecycle_phase, outcome, reason_code, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)"
        )
        action_name = "engage_control_switch" if after == 1 else "release_control_switch"
        stmt.execute([
            event_id,
            tenant_id,
            switch,
            before,
            after,
            revision,
            "aporia.control_switch.changed",
            self.environment.app_environment(),
            "admin",
            "audit",
            "aporia-independent-control",
            operation_id,
            "admin",
            operator_user_id,
            "aporia_control_switch",
            action_name,
            "succeeded",
            "succeeded",
            reason_code,
        ])

    def _validate(self, tenant_id: int, switch: str) -> None:
        if tenant_id < 0 or switch not in self.SWITCHES:
            raise ValueError("aporia_control_switch_invalid")
        if switch == self.TENANT and tenant_id < 1:
            raise ValueError("aporia_control_switch_invalid")

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
