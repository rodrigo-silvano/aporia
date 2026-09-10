#!/usr/bin/env python3
"""
APORIA Ecological Session Context Backfill CLI.
Enforces retroactive linking prohibition for ecological trials.
"""

from __future__ import annotations
import sys
import os
import json

from aporia.infrastructure.db import get_connection
from aporia.application.ecological_backfill import AporiaEcologicalSessionContextBackfill


def main() -> int:
    pdo = get_connection()
    env = os.getenv("APP_ENV", "unknown")
    try:
        backfill = AporiaEcologicalSessionContextBackfill(pdo, env)
        result = backfill.run()
        print(json.dumps({"ok": True, **result}, separators=(",", ":")))
        return 0
    except Exception as exc:
        err_msg = str(exc) if isinstance(exc, RuntimeError) else "aporia_ecological_session_context_backfill_failed"
        sys.stderr.write(json.dumps({"ok": False, "error": err_msg}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
