from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .protocol import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS, report_dict, run_protocol
from .sealing import assert_current_seal, code_commitment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    assert_current_seal(root)
    if arguments.mode == "validate":
        print(_render(_payload(report_dict(run_protocol("development", DEVELOPMENT_SEEDS)))), end="")
        return 0
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("operational_r3_staging_required")
    if not arguments.output or not arguments.execution_ledger:
        raise RuntimeError("operational_r3_confirmatory_paths_required")
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink() or output.parent != ledger.parent:
        raise RuntimeError("operational_r3_confirmatory_already_executed")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        rendered = _render(_payload(report_dict(run_protocol("confirmation", CONFIRMATION_SEEDS))))
        output.write_text(rendered, encoding="utf-8")
        os.chmod(output, 0o600)
        receipt = {
            "protocol_key": "aporia_operational_r3",
            "execution_count": 1,
            "environment": "staging",
            "status": "completed",
            "rerun_allowed": False,
            "output_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "code_commitment": code_commitment(root),
            "production_eligible": False,
        }
    except BaseException as exception:
        receipt = {
            "protocol_key": "aporia_operational_r3",
            "execution_count": 1,
            "environment": "staging",
            "status": "invalidated",
            "rerun_allowed": False,
            "error_class": exception.__class__.__name__,
            "code_commitment": code_commitment(root),
        }
        ledger.write_text(_render(receipt), encoding="utf-8")
        os.chmod(ledger, 0o600)
        raise
    ledger.write_text(_render(receipt), encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0


def _payload(report: dict[str, object]) -> dict[str, object]:
    return {
        "protocol_key": "aporia_operational_r3",
        "extends_without_repeating": "aporia_operational_conformance_r2",
        "report": report,
        "eligible": report["passed"] is True,
        "production_eligible": False,
    }


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
