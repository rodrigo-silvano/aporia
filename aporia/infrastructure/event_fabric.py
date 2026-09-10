"""
Aporia Causal Event Fabric.
Implements Hybrid Logical Clock (HLC), causal DAG lineage tracking,
deterministic canonical verification, idempotent ingestion, outbox delivery,
event replay and concurrency metrics.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Mapping, Sequence
from aporia.crypto import canonical_json, hmac_sha256_hex, sha256_hex, hash_equals
from aporia.infrastructure.db import Connection


class AporiaEventFabric:
    CANONICAL_TENANT_ROLES = ("owner", "full", "limited")
    CANONICAL_PRIVACY_SCOPES = (
        "GLOBAL_PUBLIC",
        "TENANT_PRIVATE",
        "USER_PRIVATE",
        "PLATFORM_INTERNAL",
    )

    FIELDS = (
        "schema_version",
        "event_id",
        "tenant_id",
        "session_ref",
        "task_ref",
        "event_kind",
        "source_service",
        "occurred_at",
        "journal_sequence",
        "attributes",
        "canonical_sha256",
        "completeness",
    )

    ATTRIBUTES: dict[str, list[list[Any]]] = {
        "turn.started": [],
        "tool.proposed": [],
        "approval.requested": [["approval_required", True]],
        "effect.started": [],
        "effect.completed": [["terminal", True]],
        "turn.completed": [["terminal", True]],
        "turn.failed": [["terminal", True], ["outcome", "failed"]],
        "turn.interrupted": [["terminal", True], ["outcome", "failed"]],
        "turn.deduplicated": [],
    }

    def __init__(
        self,
        conn: Connection | Any,
        secret: str,
        allowed_source_services: Sequence[str] | None = None,
    ) -> None:
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.secret = secret
        self.allowed_source_services = set(allowed_source_services) if allowed_source_services else {"agent-gateway", "generic-harness", "runtime-gateway"}

    def ingest(self, context: Mapping[str, Any], input_data: Mapping[str, Any], now_us: int | None = None) -> dict[str, Any]:
        tenant_id = self._canonical_tenant_id(context)
        self._verify_session_context(context)
        event = self._validate(context, input_data, tenant_id)

        existing = self._existing(tenant_id, str(event["event_id"]))
        if existing:
            return self._deduplicated(existing, str(event["canonical_sha256"]))

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            self._ensure_clock(tenant_id)
            clock = self._lock_clock(tenant_id)
            parent = self._session_head(tenant_id, int(context["id"]))

            current_time_us = now_us if now_us is not None else int(time.time() * 1_000_000)
            wall_us = max(
                current_time_us,
                int(clock["hlc_wall_us"]),
                int(parent["hlc_wall_us"]) if parent else 0,
            )
            logical = (int(clock["hlc_logical"]) + 1) if wall_us == int(clock["hlc_wall_us"]) else 0
            causal_depth = 1 if parent is None else (int(parent["causal_depth"]) + 1)

            self._update_clock(tenant_id, wall_us, logical)
            event_row_id = self._insert_event(context, event, tenant_id, wall_us, logical, causal_depth)

            # Ensure event_parents table exists
            self.conn.exec(
                "CREATE TABLE IF NOT EXISTS aporia_event_parents ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, "
                "child_event_id INTEGER NOT NULL, parent_event_id INTEGER NOT NULL, "
                "relation_type TEXT NOT NULL, created_at TEXT, updated_at TEXT)"
            )
            if parent is not None:
                stmt_parent = self.conn.prepare(
                    "INSERT INTO aporia_event_parents "
                    "(tenant_id, child_event_id, parent_event_id, relation_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'session_predecessor', ?, ?)"
                )
                now_str = self._database_timestamp(now_us)
                stmt_parent.execute([tenant_id, event_row_id, int(parent["id"]), now_str, now_str])

            # Ensure event_outbox table exists
            self.conn.exec(
                "CREATE TABLE IF NOT EXISTS aporia_event_outbox ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, "
                "event_id INTEGER NOT NULL, delivery_key TEXT NOT NULL, status TEXT NOT NULL, "
                "fencing_token INTEGER NOT NULL, attempts INTEGER NOT NULL, created_at TEXT, updated_at TEXT)"
            )
            stmt_outbox = self.conn.prepare(
                "INSERT INTO aporia_event_outbox "
                "(tenant_id, event_id, delivery_key, status, fencing_token, attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', 0, 0, ?, ?)"
            )
            now_str = self._database_timestamp(now_us)
            delivery_key = sha256_hex(f"aporia:{tenant_id}:{event['event_id']}:projection:v2")
            stmt_outbox.execute([tenant_id, event_row_id, delivery_key, now_str, now_str])

            if started:
                self.conn.commit()

            return {
                "deduplicated": False,
                "event_row_id": event_row_id,
                "event_id": str(event["event_id"]),
                "hlc": {"wall_us": wall_us, "logical": logical},
                "parent_count": 0 if parent is None else 1,
                "causal_depth": causal_depth,
            }
        except Exception as exc:
            if started and self.conn.in_transaction():
                self.conn.roll_back()

            existing = self._existing(tenant_id, str(event["event_id"]))
            if existing:
                return self._deduplicated(existing, str(event["canonical_sha256"]))

            if self._sequence_occupied(tenant_id, str(event["session_ref"]), int(event["journal_sequence"])):
                raise RuntimeError("aporia_event_sequence_conflict") from exc

            raise

    def replay(self, context: Mapping[str, Any], cursor: Mapping[str, Any], limit: int) -> dict[str, Any]:
        tenant_id = self._canonical_tenant_id(context)
        wall_us = max(0, int(cursor.get("wall_us", 0)))
        logical = max(0, int(cursor.get("logical", 0)))
        event_id = str(cursor.get("event_id", "")).strip()
        clamped_limit = min(200, max(1, limit))

        # Ensure aporia_event_parents table exists for LEFT JOIN
        self.conn.exec(
            "CREATE TABLE IF NOT EXISTS aporia_event_parents ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, "
            "child_event_id INTEGER NOT NULL, parent_event_id INTEGER NOT NULL, "
            "relation_type TEXT NOT NULL, created_at TEXT, updated_at TEXT)"
        )

        sql = f"""
            SELECT event.event_id, event.event_kind, event.occurred_at, event.journal_sequence,
                   event.hlc_wall_us, event.hlc_logical, event.causal_depth, event.attributes_json, event.canonical_sha256,
                   event.completeness, event.schema_version, parent.event_id AS parent_event_id
            FROM aporia_events event
            LEFT JOIN aporia_event_parents edge ON edge.child_event_id = event.id
            LEFT JOIN aporia_events parent ON parent.id = edge.parent_event_id AND parent.tenant_id = event.tenant_id
            WHERE event.tenant_id = ?
              AND (event.hlc_wall_us > ?
                   OR (event.hlc_wall_us = ? AND event.hlc_logical > ?)
                   OR (event.hlc_wall_us = ? AND event.hlc_logical = ? AND event.event_id > ?))
            ORDER BY event.hlc_wall_us, event.hlc_logical, event.event_id
            LIMIT {clamped_limit}
        """
        stmt = self.conn.prepare(sql)
        stmt.execute([tenant_id, wall_us, wall_us, logical, wall_us, logical, event_id])
        events: list[dict[str, Any]] = []
        for row in stmt.fetchAll():
            events.append({
                "schema_version": int(row["schema_version"]),
                "event_id": str(row["event_id"]),
                "event_kind": str(row["event_kind"]),
                "occurred_at": str(row["occurred_at"]),
                "journal_sequence": int(row["journal_sequence"]),
                "hlc": {"wall_us": int(row["hlc_wall_us"]), "logical": int(row["hlc_logical"])},
                "causal_depth": int(row["causal_depth"]),
                "attributes": json.loads(str(row["attributes_json"])),
                "canonical_sha256": str(row["canonical_sha256"]),
                "completeness": str(row["completeness"]),
                "causal_parent_ids": [str(row["parent_event_id"])] if row.get("parent_event_id") else [],
            })

        last = events[-1] if events else None
        next_cursor = (
            {
                "wall_us": last["hlc"]["wall_us"],
                "logical": last["hlc"]["logical"],
                "event_id": last["event_id"],
            }
            if last
            else None
        )
        return {"events": events, "next_cursor": next_cursor}

    def metrics(self, context: Mapping[str, Any]) -> dict[str, Any]:
        tenant_id = self._canonical_tenant_id(context)
        stmt = self.conn.prepare(
            "SELECT COUNT(*) AS event_count, "
            "       SUM(CASE WHEN causal_depth > 1 THEN 1 ELSE 0 END) AS rooted_event_count, "
            "       MAX(causal_depth) AS maximum_depth "
            "FROM aporia_events WHERE tenant_id = ?"
        )
        stmt.execute([tenant_id])
        totals = stmt.fetch() or {}
        event_count = int(totals.get("event_count") or 0)
        rooted_event_count = int(totals.get("rooted_event_count") or 0)
        max_depth = int(totals.get("maximum_depth") or 0)

        stmt2 = self.conn.prepare(
            "SELECT COUNT(*) AS session_events FROM aporia_events WHERE tenant_id = ? GROUP BY runtime_session_id"
        )
        stmt2.execute([tenant_id])
        total_pairs = (event_count * max(0, event_count - 1)) // 2
        comparable_pairs = 0
        for row in stmt2.fetchAll():
            count = int(row.get("session_events", 0) or 0)
            comparable_pairs += (count * max(0, count - 1)) // 2

        return {
            "event_count": event_count,
            "rooted_event_count": rooted_event_count,
            "maximum_depth": max_depth,
            "causal_coverage": (rooted_event_count / event_count) if event_count > 0 else 0.0,
            "concurrency_ratio": ((total_pairs - comparable_pairs) / total_pairs) if total_pairs > 0 else 0.0,
        }

    def _validate(self, context: Mapping[str, Any], input_data: Mapping[str, Any], tenant_id: int) -> dict[str, Any]:
        inp_keys = sorted(input_data.keys())
        exp_keys = sorted(self.FIELDS)
        if inp_keys != exp_keys:
            raise RuntimeError("aporia_event_schema_invalid")

        if int(input_data.get("tenant_id", 0)) != tenant_id:
            raise RuntimeError("aporia_tenant_forbidden")

        if int(input_data.get("schema_version", 0)) != 1:
            raise RuntimeError("aporia_event_version_invalid")

        event_id = str(input_data.get("event_id", "")).strip()
        if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", event_id, re.I):
            raise RuntimeError("aporia_event_id_invalid")

        event_kind = str(input_data.get("event_kind", "")).strip()
        if event_kind not in self.ATTRIBUTES:
            raise RuntimeError("aporia_event_kind_invalid")

        attributes = input_data.get("attributes")
        if not isinstance(attributes, list):
            raise RuntimeError("aporia_event_attributes_invalid")

        if attributes != self.ATTRIBUTES[event_kind]:
            raise RuntimeError("aporia_event_attributes_invalid")

        public_session_id = str(context.get("public_id", "")).strip()
        turn_id = str(context.get("turn_id", "")).strip()
        session_ref = str(input_data.get("session_ref", "")).strip()
        task_ref = str(input_data["task_ref"]).strip() if input_data.get("task_ref") is not None else None

        if not hash_equals(self.opaque_ref(tenant_id, "session", public_session_id), session_ref):
            raise RuntimeError("aporia_session_forbidden")

        expected_task_ref = self.opaque_ref(tenant_id, "task", turn_id) if turn_id else None
        if task_ref != expected_task_ref:
            raise RuntimeError("aporia_task_forbidden")

        source_service = str(input_data.get("source_service", "")).strip()
        # Check source service validity
        if not self._is_source_service_allowed(source_service) or input_data.get("completeness") != "complete":
            raise RuntimeError("aporia_event_source_invalid")

        sequence = input_data.get("journal_sequence")
        if not isinstance(sequence, int) or sequence < 1 or sequence > 1_000_000_000:
            raise RuntimeError("aporia_event_sequence_invalid")

        occurred_at = str(input_data.get("occurred_at", "")).strip()
        if len(occurred_at) > 40 or not re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$",
            occurred_at,
        ):
            raise RuntimeError("aporia_event_time_invalid")

        try:
            # Parse ISO datetime
            iso_str = occurred_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str).astimezone(timezone.utc)
            occurred_db = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        except Exception:
            raise RuntimeError("aporia_event_time_invalid")

        canonical = dict(input_data)
        canonical.pop("canonical_sha256", None)
        canonical_str = json.dumps(self._canonicalize(canonical), ensure_ascii=False, separators=(",", ":"))
        digest = str(input_data.get("canonical_sha256", "")).strip().lower()

        if not re.match(r"^[0-9a-f]{64}$", digest) or not hash_equals(sha256_hex(canonical_str), digest):
            raise RuntimeError("aporia_event_integrity_invalid")

        result = dict(input_data)
        result["occurred_database"] = occurred_db
        return result

    def _is_source_service_allowed(self, source_service: str) -> bool:
        if "*" in self.allowed_source_services or bool(self.allowed_source_services and source_service in self.allowed_source_services):
            return True
        # Also allow standard generic agent harnesses
        if source_service in ("agent-gateway", "generic-harness", "runtime-gateway"):
            return True
        return False

    def _existing(self, tenant_id: int, event_id: str) -> dict[str, Any] | None:
        stmt = self.conn.prepare(
            "SELECT id, event_id, canonical_sha256, hlc_wall_us, hlc_logical "
            "FROM aporia_events WHERE tenant_id = ? AND event_id = ? LIMIT 1"
        )
        stmt.execute([tenant_id, event_id])
        return stmt.fetch()

    def _deduplicated(self, existing: Mapping[str, Any], digest: str) -> dict[str, Any]:
        if not hash_equals(str(existing["canonical_sha256"]), digest):
            raise RuntimeError("aporia_event_conflict")
        return {
            "deduplicated": True,
            "event_row_id": int(existing["id"]),
            "event_id": str(existing["event_id"]),
            "hlc": {"wall_us": int(existing["hlc_wall_us"]), "logical": int(existing["hlc_logical"])},
        }

    def _sequence_occupied(self, tenant_id: int, session_ref: str, sequence: int) -> bool:
        stmt = self.conn.prepare(
            "SELECT 1 FROM aporia_events WHERE tenant_id = ? AND session_ref = ? AND journal_sequence = ? LIMIT 1"
        )
        stmt.execute([tenant_id, session_ref, sequence])
        return stmt.fetch() is not None

    def _ensure_clock(self, tenant_id: int) -> None:
        stmt = self.conn.prepare(
            "INSERT OR IGNORE INTO aporia_tenant_clocks (tenant_id, hlc_wall_us, hlc_logical, created_at, updated_at) "
            "VALUES (?, 0, 0, ?, ?)"
        )
        now = self._database_timestamp(None)
        stmt.execute([tenant_id, now, now])

    def _lock_clock(self, tenant_id: int) -> dict[str, Any]:
        stmt = self.conn.prepare(
            "SELECT hlc_wall_us, hlc_logical FROM aporia_tenant_clocks WHERE tenant_id = ?"
        )
        stmt.execute([tenant_id])
        clock = stmt.fetch()
        if not clock:
            raise RuntimeError("aporia_clock_unavailable")
        return clock

    def _update_clock(self, tenant_id: int, wall_us: int, logical: int) -> None:
        stmt = self.conn.prepare(
            "UPDATE aporia_tenant_clocks SET hlc_wall_us = ?, hlc_logical = ?, updated_at = ? WHERE tenant_id = ?"
        )
        stmt.execute([wall_us, logical, self._database_timestamp(wall_us), tenant_id])

    def _session_head(self, tenant_id: int, session_id: int) -> dict[str, Any] | None:
        stmt = self.conn.prepare(
            "SELECT id, event_id, hlc_wall_us, hlc_logical, causal_depth FROM aporia_events "
            "WHERE tenant_id = ? AND runtime_session_id = ? "
            "ORDER BY hlc_wall_us DESC, hlc_logical DESC, event_id DESC LIMIT 1"
        )
        stmt.execute([tenant_id, session_id])
        return stmt.fetch()

    def _insert_event(
        self,
        context: Mapping[str, Any],
        event: Mapping[str, Any],
        tenant_id: int,
        wall_us: int,
        logical: int,
        causal_depth: int,
    ) -> int:
        now_str = self._database_timestamp(wall_us)
        attrs_json = json.dumps(event["attributes"], ensure_ascii=False, separators=(",", ":"))
        stmt = self.conn.prepare(
            "INSERT INTO aporia_events "
            "(event_id, tenant_id, runtime_session_id, session_ref, task_ref, event_kind, source_service, "
            " occurred_at, journal_sequence, ingested_at, hlc_wall_us, hlc_logical, causal_depth, attributes_json, "
            " canonical_sha256, completeness, schema_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        stmt.execute([
            str(event["event_id"]),
            tenant_id,
            int(context["id"]),
            str(event["session_ref"]),
            event["task_ref"],
            str(event["event_kind"]),
            str(event["source_service"]),
            str(event["occurred_database"]),
            int(event["journal_sequence"]),
            now_str,
            wall_us,
            logical,
            causal_depth,
            attrs_json,
            str(event["canonical_sha256"]),
            str(event["completeness"]),
            int(event["schema_version"]),
            now_str,
            now_str,
        ])
        return self.conn.last_insert_id()

    def _canonical_tenant_id(self, context: Mapping[str, Any]) -> int:
        tenant_id = context.get("tenant_id")
        if (
            not self._positive_integer(tenant_id)
            or not self._positive_integer(context.get("membership_id"))
            or not self._positive_integer(context.get("owner_user_id"))
            or not self._positive_integer(context.get("actor_user_id"))
            or not self._positive_integer(context.get("context_revision"))
            or context.get("tenant_context_status") != "resolved"
            or context.get("tenant_context_source") != "canonical"
            or context.get("role") not in self.CANONICAL_TENANT_ROLES
            or context.get("privacy_scope") not in self.CANONICAL_PRIVACY_SCOPES
        ):
            raise RuntimeError("aporia_tenant_context_required")
        return int(tenant_id)

    def _verify_session_context(self, context: Mapping[str, Any]) -> None:
        if not self._positive_integer(context.get("id")):
            raise RuntimeError("runtime_session_invalid")
        public_id = str(context.get("public_id", "")).strip()
        if not public_id:
            raise RuntimeError("runtime_session_invalid")

        stmt = self.conn.prepare(
            "SELECT public_id, owner_user_id FROM assistant_runtime_sessions "
            "WHERE id = ? AND owner_user_id = ? AND status IN ('active', 'archived') "
            "LIMIT 1"
        )
        stmt.execute([int(context["id"]), int(context["owner_user_id"])])
        row = stmt.fetch()
        if not row or not hash_equals(str(row.get("public_id", "")), public_id):
            raise RuntimeError("runtime_session_invalid")

    @staticmethod
    def _positive_integer(value: Any) -> bool:
        if isinstance(value, int) and value > 0:
            return True
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return True
        return False

    def opaque_ref(self, tenant_id: int, kind: str, value: str) -> str:
        return hmac_sha256_hex(self.secret, f"aporia:{tenant_id}:{kind}:{value}")

    def opaqueRef(self, tenant_id: int, kind: str, value: str) -> str:
        return self.opaque_ref(tenant_id, kind, value)

    @classmethod
    def _canonicalize(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: cls._canonicalize(value[k]) for k in sorted(value.keys())}
        if isinstance(value, list):
            return [cls._canonicalize(item) for item in value]
        return value

    @staticmethod
    def _database_timestamp(microseconds: int | None) -> str:
        val = (microseconds if microseconds is not None else int(time.time() * 1_000_000)) / 1_000_000.0
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
