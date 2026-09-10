#!/usr/bin/env python3
"""
APORIA Product Health and Operational Metrics CLI.
Generates operational health report for tenants.
"""

from __future__ import annotations
import sys
import os
import json

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.product_state import AporiaProductOperationalMetrics


def main() -> int:
    base_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tenant_id = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() != "" else None
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24

    pdo = get_connection()
    metrics = AporiaProductOperationalMetrics(pdo)
    report = metrics.report(tenant_id, hours)
    print(json.dumps(report, indent=2, separators=(",", ": "), ensure_ascii=False))
    return 1 if report.get("status") == "critical" else 0


if __name__ == "__main__":
    sys.exit(main())
