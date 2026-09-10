from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .chaos import run_chaos_r2
from .poisoning import run_poisoning_r2
from .sealing import assert_current_seal, code_commitment


DEVELOPMENT_SEED = 240822
CONFIRMATION_SEED = 250822


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    seed = DEVELOPMENT_SEED if arguments.mode == "validate" else CONFIRMATION_SEED
    ledger = None
    output = None
    if arguments.mode == "confirmatory":
        if os.environ.get("APP_ENV", "").strip().lower() != "staging":
            raise RuntimeError("operational_r2_staging_required")
        if not arguments.output or not arguments.execution_ledger:
            raise RuntimeError("operational_r2_confirmatory_paths_required")
        output = Path(arguments.output).resolve()
        ledger = Path(arguments.execution_ledger).resolve()
        if output.parent != ledger.parent or output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
            raise RuntimeError("operational_r2_confirmatory_already_executed")
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    chaos = run_chaos_r2(seed=seed)
    poisoning = run_poisoning_r2()
    eligible = chaos.passed() and poisoning.passed()
    payload = {
        "protocol_key": "aporia_operational_conformance_r2",
        "phase": "development" if arguments.mode == "validate" else "confirmation",
        "chaos": chaos.__dict__,
        "poisoning": poisoning.__dict__,
        "eligible": eligible,
        "first_failed_gate": None if eligible else "chaos" if not chaos.passed() else "poisoning",
        "production_eligible": False,
        "scientific_claim": "operational_state_machine_and_persistence_safety_only" if eligible else "operational_conformance_not_demonstrated",
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if arguments.mode == "validate":
        if arguments.output:
            Path(arguments.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if eligible else 1
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_operational_conformance_r2",
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "code_commitment": code_commitment(repository_root),
        "eligible": eligible,
    }
    ledger.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0 if eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
