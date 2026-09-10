#!/usr/bin/env python3
"""APORIA Control Plane Query — Displays kill switch state and runtime mode for a tenant."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aporia.config import Environment
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.application.runtime_mode_resolver import AporiaRuntimeModeResolver


def main() -> None:
    parser = argparse.ArgumentParser(description="Query APORIA control plane state")
    parser.add_argument("--tenant-id", type=int, default=1, help="Tenant ID to query")
    args = parser.parse_args()

    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    env = Environment()

    controls = AporiaIndependentControlPlane(conn, env)
    snapshot = controls.snapshot(args.tenant_id)
    mode = AporiaRuntimeModeResolver.for_tenant(env, conn, args.tenant_id)

    report = {
        "tenant_id": args.tenant_id,
        "runtime_mode": mode,
        "kill_switches": snapshot,
        "any_critical_engaged": controls.any_engaged(
            args.tenant_id, ["global", "tenant", "runtime_influence"]
        ),
    }

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
