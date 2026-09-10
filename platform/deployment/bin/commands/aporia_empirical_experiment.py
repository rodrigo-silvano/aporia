#!/usr/bin/env python3
"""
APORIA Empirical Experiment CLI.
Coordinates preparation, ingestion, summary and correction of empirical experiments.
"""

from __future__ import annotations
import sys
import os
import re
import json
import argparse

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.experiments import AporiaEmpiricalStudy


def main() -> int:
    parser = argparse.ArgumentParser(description="APORIA Empirical Experiment")
    parser.add_argument("--action", required=True, choices=["prepare", "ingest", "summary", "correct-bootstrap"])
    parser.add_argument("--tenant", type=int, required=True)
    parser.add_argument("--run-id", default="", help="Run ID")
    parser.add_argument("--result", default="", help="Result JSON filepath")
    parser.add_argument("--protocol-version", default="v6", choices=["v2", "v3", "v4", "v5", "v6"])
    args = parser.parse_args()

    operator_id = int(os.getenv("APORIA_OPERATOR_ADMIN_ID", os.getenv("PLS_OPERATOR_ADMIN_ID", "0")))
    if args.tenant < 9000000000000000000 or operator_id < 1:
        sys.stderr.write(json.dumps({"ok": False, "error": "validation_failed"}) + "\n")
        return 2

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    fixture_path = os.path.join(base_dir, "experiments", "aporia-lacuna", "fixtures", f"aporia_c0_c5_{args.protocol_version}.json")
    env = os.getenv("APP_ENV", "unknown")
    pdo = get_connection()
    study = AporiaEmpiricalStudy(pdo, fixture_path, env)

    try:
        if args.action == "prepare":
            result = study.prepare(args.tenant, operator_id)
        else:
            run_id = args.run_id.lower().strip()
            if not re.match(r"^[0-9a-f-]{36}$", run_id):
                raise RuntimeError("aporia_empirical_run_invalid")
            if args.action == "summary":
                result = study.summary(args.tenant, run_id)
            elif args.action == "correct-bootstrap":
                result = study.correct_bootstrap(args.tenant, operator_id, run_id)
            else:
                result_path = args.result.strip()
                if not result_path or not os.path.isfile(result_path) or os.path.islink(result_path):
                    raise RuntimeError("aporia_empirical_result_unavailable")
                if not re.match(r"^/tmp/aporia-experiment-[a-z0-9-]{1,80}\.json$", os.path.abspath(result_path)):
                    raise RuntimeError("aporia_empirical_result_unavailable")
                if os.path.getsize(result_path) > 2097152:
                    raise RuntimeError("aporia_empirical_result_unavailable")
                with open(result_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if not isinstance(payload, dict):
                    raise RuntimeError("aporia_empirical_result_invalid")
                result = study.ingest(args.tenant, operator_id, run_id, payload)

        print(json.dumps({"ok": True, "data": result}, separators=(",", ":"), ensure_ascii=False))
        return 0
    except Exception as exc:
        err_msg = str(exc) if isinstance(exc, RuntimeError) else "aporia_empirical_failed"
        sys.stderr.write(json.dumps({"ok": False, "error": err_msg}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
