from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .contracts import ReplicationConfig
from .capacity import neutral_capacity_report, predictive_information_differences
from .calibration import calibrated_power_plan
from .ecological import run_synthetic_ecological_study
from .generalization import FAMILIES, WorldFamily
from .lifecycle import run_chaos
from .poisoning import poisoning_matrix
from .protocols import all_discriminative_protocols
from .sealing import assert_current_seal
from .study import evaluate_replication, run_replication


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    if arguments.mode == "validate":
        payload = validation_payload()
    else:
        executed = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_replication_r1_result.json"
        if executed.is_file():
            raise RuntimeError("replication_confirmatory_already_executed")
        if not arguments.output or not arguments.execution_ledger:
            raise RuntimeError("replication_confirmatory_paths_required")
        output = Path(arguments.output)
        ledger = Path(arguments.execution_ledger)
        if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
            raise RuntimeError("replication_confirmatory_already_executed")
        if output.parent.resolve() != ledger.parent.resolve():
            raise RuntimeError("replication_confirmatory_storage_mismatch")
        power_plan = calibrated_power_plan()
        if not power_plan.complete:
            raise RuntimeError("replication_power_plan_incomplete")
        config = ReplicationConfig()
        results = run_replication(config)
        capacity = neutral_capacity_report(config.seeds)
        report = evaluate_replication(
            results,
            config,
            predictive_information_differences(config.seeds),
            capacity.differences,
            capacity.critical_unsafe_commits,
        )
        payload = report.as_dict() | validation_payload() | {"power_plan": power_plan.as_dict()}
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        output.write_text(rendered, encoding="utf-8")
        os.chmod(output, 0o600)
        receipt = {
            "protocol_key": report.preregistration_key,
            "design_commitment": report.design_commitment,
            "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "execution_count": 1,
            "environment": "staging",
        }
        ledger.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(ledger, 0o600)
    if arguments.mode == "validate":
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if arguments.output:
            Path(arguments.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
    return 0


def validation_payload() -> dict[str, object]:
    protocols = all_discriminative_protocols()
    chaos = run_chaos()
    poisoning = poisoning_matrix()
    ecological = run_synthetic_ecological_study()
    power_plan = calibrated_power_plan(simulations=1000)
    return {
        "discriminative_protocols": [
            {"name": item.name, "passed": item.passed, "metrics": item.metrics}
            for item in protocols
        ],
        "generalization_development_families": [item.family.value for item in FAMILIES if item.development_allowed],
        "hidden_confirmation_family": WorldFamily.G8_ASYMMETRIC.value,
        "generalization_execution": "staging_only_separate_runner",
        "chaos": chaos.__dict__,
        "poisoning": {
            name: {"accepted": decision.accepted, "reason_code": decision.reason_code}
            for name, decision in poisoning.items()
        },
        "ecological_synthetic": ecological.__dict__,
        "ecological_account_49": "staging_shadow_runtime_only",
        "power_plan_validation": power_plan.as_dict(),
        "scientific_claim": "not_established_by_validation_or_synthetic_execution",
        "production_eligible": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
