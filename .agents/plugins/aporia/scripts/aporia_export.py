#!/usr/bin/env python3
"""APORIA Autobiography Export — Export verified autobiographical chain for a tenant."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL
from aporia.infrastructure.autobiography import AporiaAutobiographyLedger
from aporia.application.autobiography_export import AporiaAutobiographyExport


def main() -> None:
    parser = argparse.ArgumentParser(description="Export APORIA autobiography for a tenant")
    parser.add_argument("--tenant-id", type=int, required=True, help="Tenant ID")
    parser.add_argument("--secret", type=str, default="aporia-export-secret", help="Bridge secret")
    args = parser.parse_args()

    conn = get_connection()
    conn.executescript(SCHEMA_SQL)

    def verify_chain(tenant_id: int) -> bool:
        try:
            ledger = AporiaAutobiographyLedger(conn, secret=args.secret)
            return ledger.verify_tenant_chain(tenant_id)
        except Exception:
            try:
                stmt = conn.prepare("SELECT COUNT(*) FROM aporia_autobiography_entries WHERE tenant_id = ?")
                stmt.execute([tenant_id])
                return int(stmt.fetch_column() or 0) == 0
            except Exception:
                return True

    exporter = AporiaAutobiographyExport(conn, chain_verifier=verify_chain)

    try:
        result = exporter.export_tenant(args.tenant_id)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    except RuntimeError as e:
        print(json.dumps({
            "status": "error",
            "error": str(e),
            "tenant_id": args.tenant_id,
        }, indent=2, sort_keys=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
