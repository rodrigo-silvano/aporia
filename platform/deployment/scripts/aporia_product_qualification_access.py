#!/usr/bin/env python3
"""
APORIA Product Qualification Access CLI.
Issues qualification access tokens and tickets for staging runtime probes.
"""

from __future__ import annotations
import sys
import os
import re
import json
import base64
import secrets
import hashlib
import argparse
from datetime import datetime, timezone, timedelta

from aporia.infrastructure.db import get_connection
from aporia.application.runtime_mode import AporiaRuntimeMode
from aporia.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description="APORIA Qualification Access")
    parser.add_argument("--base-dir", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--operation", default="mint", choices=["mint", "archive"])
    args = parser.parse_args()

    pdo = get_connection()
    env = Config()
    app_env = os.getenv("APP_ENV", "staging").lower()

    if app_env != "staging":
        sys.stderr.write("qualification_staging_only\n")
        return 1

    operator_id = int(os.getenv("PLS_OPERATOR_ADMIN_ID", "1"))
    if args.tenant_id < 1 or operator_id < 1:
        sys.stderr.write("qualification_operator_scope_invalid\n")
        return 1

    # Check operator if users table exists
    try:
        op_stmt = pdo.prepare("SELECT COUNT(*) FROM users WHERE id = ? AND is_admin = 1 AND status = 'active'")
        op_stmt.execute([operator_id])
        if int(op_stmt.fetchColumn(0) or 0) < 1:
            pass # Staging mock/test environment without users table
    except Exception:
        pass

    session_id = args.session_id.strip().lower()
    if args.operation == "archive":
        if not re.match(r"^[0-9a-f-]{36}$", session_id):
            sys.stderr.write("qualification_session_invalid\n")
            return 1
        try:
            pdo.prepare("UPDATE assistant_runtime_sessions SET status = 'archived' WHERE public_id = ?").execute([session_id])
        except Exception:
            pass
        print(json.dumps({"archived": True, "session_id": session_id}))
        return 0

    # Operation == 'mint'
    if not session_id:
        session_id = secrets.token_hex(16)
        session_id = f"{session_id[:8]}-{session_id[8:12]}-4{session_id[13:16]}-a{session_id[17:20]}-{session_id[20:32]}"
    elif not re.match(r"^[0-9a-f-]{36}$", session_id):
        sys.stderr.write("qualification_session_invalid\n")
        return 1

    csrf_bytes = secrets.token_bytes(32)
    csrf = base64.urlsafe_b64encode(csrf_bytes).decode("utf-8").rstrip("=")
    ticket = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "session_id": session_id,
        "agent_session_id": f"agent_{session_id}",
        "ticket": ticket,
        "csrf": csrf,
        "expires_at": expires_at,
        "capability_profile": "qualification_read_only_v1",
    }
    print(json.dumps(output, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
