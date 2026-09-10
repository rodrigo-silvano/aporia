from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from replication_r1.statistics import equivalence

from .safety import DEVELOPMENT_SEEDS, run_safety_development
from .sealing import assert_current_seal


CONFIRMATION_SEEDS = tuple(range(43001, 45561))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    if arguments.mode == "validate":
        payload = evaluate(DEVELOPMENT_SEEDS, "development")
        if arguments.output:
            Path(arguments.output).write_text(_render(payload), encoding="utf-8")
        else:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    if not arguments.output or not arguments.execution_ledger:
        raise RuntimeError("post_r1_safety_confirmatory_paths_required")
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    _assert_fresh(output, ledger)
    payload = evaluate(CONFIRMATION_SEEDS, "corrective_confirmation")
    rendered = _render(payload)
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_post_r1_safety_v1",
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
    }
    ledger.write_text(_render(receipt), encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0


def evaluate(seeds: tuple[int, ...], phase: str) -> dict[str, object]:
    report = run_safety_development(seeds)
    capacity = equivalence("neutral_capacity", report.capacity_differences, 0.01, 0.005)
    first_failed = None
    if report.critical_unsafe_commits != 0:
        first_failed = "zero_critical_unsafe_commits"
    elif not capacity.passed:
        first_failed = "neutral_capacity_equivalence"
    return {
        "protocol_key": "aporia_post_r1_safety_v1",
        "phase": phase,
        "analysis_unit": "seed_block",
        "independent_blocks": report.independent_blocks,
        "decisions": report.decisions,
        "critical_unsafe_commits": report.critical_unsafe_commits,
        "capacity_equivalence": capacity.__dict__,
        "eligible": first_failed is None,
        "first_failed_gate": first_failed,
        "production_eligible": False,
        "scientific_claim": "corrected_safety_mechanism_only",
    }


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _assert_fresh(output: Path, ledger: Path) -> None:
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
        raise RuntimeError("post_r1_safety_confirmatory_already_executed")
    if output.parent != ledger.parent:
        raise RuntimeError("post_r1_safety_confirmatory_storage_mismatch")


if __name__ == "__main__":
    raise SystemExit(main())
