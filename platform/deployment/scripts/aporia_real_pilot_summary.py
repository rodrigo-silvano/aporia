#!/usr/bin/env python3
"""
APORIA Real Pilot Evaluation Summary CLI.
Summarizes pilot metrics for a specific pilot tenant.
"""

from __future__ import annotations
import sys
import os
import json

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.experiments import AporiaRealPilotEvaluation
from aporia.application.runtime_mode import AporiaRuntimeMode
from aporia.config import Config


def main() -> int:
    base_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tenant_id = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() != "" else 0

    if tenant_id < 1:
        sys.stderr.write("aporia_real_pilot_summary_invalid\n")
        return 1

    env = Config()
    if tenant_id not in AporiaRuntimeMode.pilot_tenant_ids(env):
        sys.stderr.write("aporia_real_pilot_tenant_forbidden\n")
        return 1

    pdo = get_connection()
    evaluation = AporiaRealPilotEvaluation(pdo)
    summary = evaluation.summarize(tenant_id)
    print(json.dumps(summary, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
