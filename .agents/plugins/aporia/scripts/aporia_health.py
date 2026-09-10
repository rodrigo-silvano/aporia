#!/usr/bin/env python3
"""APORIA Health Check — Verifies database, schema, and control plane status."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aporia.client import AporiaClient
from aporia.config import Environment
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane


def main() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)

    client = AporiaClient(pdo=conn)
    env = Environment()
    controls = AporiaIndependentControlPlane(conn, env)

    healthy = client.is_healthy()
    global_mode = env.get_string("APORIA_MODE", "guarded_reversible")

    # Snapshot for tenant 0 (global-only switches)
    snapshot = {}
    for switch in AporiaIndependentControlPlane.SWITCHES:
        try:
            snapshot[switch] = controls.engaged(0, switch)
        except Exception:
            snapshot[switch] = True  # fail closed

    report = {
        "status": "healthy" if healthy else "unhealthy",
        "database_connected": healthy,
        "global_mode": global_mode,
        "environment": env.app_environment(),
        "control_plane_snapshot": snapshot,
    }

    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
