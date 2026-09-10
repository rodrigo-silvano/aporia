#!/usr/bin/env python3
"""APORIA Event Ingestion CLI — Ingest a runtime event into the causal event fabric."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import uuid

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aporia.crypto import canonical_json, sha256_hex
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL
from aporia.infrastructure.event_fabric import AporiaEventFabric


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a runtime event into APORIA")
    parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    parser.add_argument("--event-kind", type=str, required=True,
                        choices=["turn.started", "tool.proposed", "approval.requested",
                                 "effect.started", "effect.completed", "turn.completed",
                                 "turn.failed", "turn.interrupted"],
                        help="Event kind")
    parser.add_argument("--session-ref", type=str, default=None, help="Session reference / public ID")
    parser.add_argument("--source-service", type=str, default="agent-gateway", help="Source service")
    parser.add_argument("--task-ref", type=str, default=None, help="Task reference / turn ID")
    parser.add_argument("--secret", type=str, default="aporia-dev-secret", help="Bridge secret")
    args = parser.parse_args()

    conn = get_connection()
    conn.executescript(SCHEMA_SQL)

    # Ensure runtime session exists for the tenant
    public_id = args.session_ref or "cli-session-default"
    stmt = conn.prepare(
        "SELECT id FROM assistant_runtime_sessions WHERE public_id = ? AND owner_user_id = ? LIMIT 1"
    )
    stmt.execute([public_id, args.tenant_id])
    session_row = stmt.fetch()
    if not session_row:
        conn.execute(
            "INSERT INTO assistant_runtime_sessions "
            "(public_id, owner_user_id, actor_user_id, agent_session_id, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            [public_id, args.tenant_id, args.tenant_id, public_id],
        )
        session_id = conn.last_insert_id()
    else:
        session_id = int(session_row["id"])

    fabric = AporiaEventFabric(conn, secret=args.secret)

    turn_id = args.task_ref or f"cli-task-{secrets.token_hex(4)}"
    session_ref = fabric.opaque_ref(args.tenant_id, "session", public_id)
    task_ref = fabric.opaque_ref(args.tenant_id, "task", turn_id)

    stmt_seq = conn.prepare(
        "SELECT MAX(journal_sequence) AS max_seq FROM aporia_events WHERE tenant_id = ? AND session_ref = ?"
    )
    stmt_seq.execute([args.tenant_id, session_ref])
    row_seq = stmt_seq.fetch()
    sequence = (int(row_seq["max_seq"]) + 1) if row_seq and row_seq.get("max_seq") is not None else 1

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    context = {
        "id": session_id,
        "public_id": public_id,
        "tenant_id": args.tenant_id,
        "owner_user_id": args.tenant_id,
        "actor_user_id": args.tenant_id,
        "membership_id": 1000 + session_id,
        "role": "owner",
        "privacy_scope": "TENANT_PRIVATE",
        "context_revision": 1,
        "tenant_context_status": "resolved",
        "tenant_context_source": "canonical",
        "turn_id": turn_id,
    }

    input_data = {
        "schema_version": 1,
        "event_id": event_id,
        "tenant_id": args.tenant_id,
        "session_ref": session_ref,
        "task_ref": task_ref,
        "event_kind": args.event_kind,
        "source_service": args.source_service,
        "occurred_at": now,
        "journal_sequence": sequence,
        "attributes": list(fabric.ATTRIBUTES[args.event_kind]),
        "completeness": "complete",
    }
    copy_ev = {k: v for k, v in input_data.items() if k != "canonical_sha256"}
    input_data["canonical_sha256"] = sha256_hex(canonical_json(copy_ev).encode("utf-8"))

    try:
        result = fabric.ingest(context, input_data)
        print(json.dumps({
            "status": "ingested",
            "event_id": event_id,
            "event_kind": args.event_kind,
            "tenant_id": args.tenant_id,
            "session_ref": session_ref,
            "journal_sequence": sequence,
            "result": result,
        }, indent=2, sort_keys=True))
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": str(e),
            "event_id": event_id,
        }, indent=2, sort_keys=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
