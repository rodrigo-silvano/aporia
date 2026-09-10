#!/usr/bin/env python3
"""
APORIA Independent Control Plane CLI Switch.
Engages or releases control switches deterministically.
"""

from __future__ import annotations
import sys
import os
import json
import argparse
import hashlib

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description="APORIA Control Switch")
    parser.add_argument("--switch", required=True, help="Switch identifier")
    parser.add_argument("--tenant", type=int, default=-1, help="Tenant ID (-1 for global)")
    parser.add_argument("--state", required=True, choices=["engaged", "released"], help="Desired state")
    parser.add_argument("--reason", default="", help="Audit reason")
    args = parser.parse_args()

    operator_user_id = int(os.getenv("APORIA_OPERATOR_ADMIN_ID", os.getenv("PLS_OPERATOR_ADMIN_ID", "0")))
    pdo = get_connection()
    env = Config()

    try:
        control_plane = AporiaIndependentControlPlane(pdo, env)
        result = control_plane.set(
            args.tenant,
            args.switch,
            args.state == "engaged",
            operator_user_id,
            args.reason,
        )
        output = {"ok": True}
        output.update(result)
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except Exception as exc:
        err_msg = str(exc) if isinstance(exc, RuntimeError) else "aporia_control_switch_failed"
        sys.stderr.write(json.dumps({"ok": False, "error": err_msg}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
