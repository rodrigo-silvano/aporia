from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .protocols import run_suite
from .sealing import assert_current_seal, code_commitment


DEVELOPMENT_SEEDS = tuple(range(60001, 60257))
CONFIRMATION_SEEDS = tuple(range(61001, 61257))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    seeds = DEVELOPMENT_SEEDS if arguments.mode == "validate" else CONFIRMATION_SEEDS
    if arguments.mode == "confirmatory":
        if os.environ.get("APP_ENV", "").strip().lower() != "staging":
            raise RuntimeError("discrimination_r2_staging_required")
        if not arguments.output or not arguments.execution_ledger:
            raise RuntimeError("discrimination_r2_confirmatory_paths_required")
        output = Path(arguments.output).resolve()
        ledger = Path(arguments.execution_ledger).resolve()
        if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink() or output.parent != ledger.parent:
            raise RuntimeError("discrimination_r2_confirmatory_already_executed")
    results = run_suite(seeds)
    first_failed = next((result.name for result in results if not result.passed), None)
    payload = {
        "protocol_key": "aporia_discrimination_r2",
        "phase": "development" if arguments.mode == "validate" else "confirmation",
        "analysis_unit": "seed_block",
        "independent_blocks": len(seeds),
        "results": [result.__dict__ for result in results],
        "eligible": first_failed is None,
        "first_failed_gate": first_failed,
        "production_eligible": False,
        "scientific_claim": "path_dependent_computational_identity_only" if first_failed is None else "discriminative_effect_not_demonstrated",
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if arguments.mode == "validate":
        if arguments.output:
            Path(arguments.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if first_failed is None else 1
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_discrimination_r2",
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "code_commitment": code_commitment(repository_root),
        "eligible": first_failed is None,
    }
    ledger.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0 if first_failed is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
