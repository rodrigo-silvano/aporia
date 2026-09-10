#!/usr/bin/env python3
"""
APORIA Ecological Outcome Backfill CLI.
Evaluates terminal relationships against ecological trial hypotheses.
"""

from __future__ import annotations
import sys
import os
import json
import hashlib

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.ecological import AporiaEcologicalOutcomeRecorder


def main() -> int:
    pdo = get_connection()
    secret = os.getenv("APORIA_BRIDGE_SECRET", "default_secret")
    recorder = AporiaEcologicalOutcomeRecorder(pdo, secret)

    try:
        stmt = pdo.prepare(
            """SELECT wr.id, wr.revision, wr.stage,
                    CASE WHEN wr.stage = 'won' THEN COALESCE(wr.won_at, wr.updated_at)
                         ELSE COALESCE(wr.lost_at, wr.updated_at) END AS outcome_at
             FROM workspace_relationships wr
             INNER JOIN workspace_profiles wp ON wp.id = wr.workspace_profile_id
             WHERE wp.user_id = 49 AND wr.relationship_type = 'lead' AND wr.stage IN ('won', 'lost')
             ORDER BY wr.id"""
        )
        stmt.execute()
        rows = stmt.fetchAll() or []
        matched = 0
        inserted = 0
        idempotent = 0

        pdo.begin_transaction()
        for row in rows:
            result = recorder.record(
                49,
                int(row["id"]),
                int(row["revision"]),
                str(row["stage"]),
                str(row["outcome_at"]) if row["outcome_at"] is not None else None,
            )
            matched += int(result.get("matched_episodes", 0))
            inserted += int(result.get("inserted_episodes", 0))
            idempotent += int(result.get("idempotent_episodes", 0))
        pdo.commit()

        print(json.dumps({
            "ok": True,
            "terminal_relationships": len(rows),
            "matched_episodes": matched,
            "inserted_episodes": inserted,
            "idempotent_episodes": idempotent,
            "runtime_influence": False,
            "external_effects": False,
        }, separators=(",", ":")))
        return 0
    except Exception as exc:
        if hasattr(pdo, "in_transaction") and pdo.in_transaction():
            pdo.roll_back()
        err_msg = str(exc) if isinstance(exc, RuntimeError) else "aporia_ecological_backfill_failed"
        sys.stderr.write(json.dumps({"ok": False, "error": err_msg}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
