from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contracts import WorldFamily
from .experiment import run_population
from .providers import AggregateHumanWorldProvider, llm_world_providers
from .sealing import assert_current_seal, code_commitment


DEVELOPMENT_SEEDS = tuple(range(70001, 70065))
CONFIRMATION_SEEDS = tuple(range(71001, 71129))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "confirmation"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execution-ledger", required=True)
    parser.add_argument("--aggregate-patterns")
    parser.add_argument("--development-output")
    parser.add_argument("--development-ledger")
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("generalization_r3_staging_required")
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    _assert_paths_fresh(output, ledger)
    output.parent.mkdir(parents=True, exist_ok=True)
    if arguments.phase == "development":
        aggregate, aggregate_commitment, providers = _development_inputs(arguments)
        reserved = _reserve(ledger)
        results = run_population(DEVELOPMENT_SEEDS, WorldFamily.G8_ASYMMETRIC, False, providers)
        source_commitment = hashlib.sha256(aggregate.read_bytes()).hexdigest()
        if source_commitment != aggregate_commitment:
            raise RuntimeError("generalization_r3_aggregate_changed_during_execution")
        seeds = DEVELOPMENT_SEEDS
    else:
        _assert_development(arguments, repository_root)
        reserved = _reserve(ledger)
        results = run_population(CONFIRMATION_SEEDS, WorldFamily.G8_ASYMMETRIC, True, {})
        source_commitment = None
        seeds = CONFIRMATION_SEEDS
    first_failed = next((item.family for item in results if not item.passed), None)
    payload = {
        "protocol_key": "aporia_generalization_r3",
        "phase": arguments.phase,
        "analysis_unit": "seed_block",
        "independent_blocks": len(seeds),
        "results": [item.__dict__ for item in results],
        "hidden_family": WorldFamily.G8_ASYMMETRIC.value,
        "aggregate_source_commitment": source_commitment,
        "personal_data_exported": False,
        "eligible": first_failed is None,
        "first_failed_gate": first_failed,
        "production_eligible": False,
        "scientific_claim": "cross_world_path_dependent_identity_effect" if first_failed is None else "generalization_not_demonstrated",
        "phenomenology_established": False,
    }
    rendered = _render(payload)
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_generalization_r3",
        "phase": arguments.phase,
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "code_commitment": code_commitment(repository_root),
        "aggregate_source_commitment": source_commitment,
        "eligible": first_failed is None,
        "families": [item.family for item in results],
        "reservation_inode": reserved,
    }
    ledger.write_text(_render(receipt), encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0 if first_failed is None else 1


def _development_inputs(arguments: argparse.Namespace) -> tuple[Path, str, dict[WorldFamily, Any]]:
    if not arguments.aggregate_patterns:
        raise RuntimeError("generalization_r3_aggregate_patterns_required")
    aggregate = _private_source(Path(arguments.aggregate_patterns))
    aggregate_commitment = hashlib.sha256(aggregate.read_bytes()).hexdigest()
    human_provider = AggregateHumanWorldProvider(aggregate)
    api_key, base_url = _credentials(Path(arguments.env_file) if arguments.env_file else None)
    if not api_key:
        raise RuntimeError("generalization_r3_openai_key_required")
    from openai import OpenAI

    options: dict[str, object] = {"api_key": api_key, "timeout": 90.0, "max_retries": 2}
    if base_url:
        options["base_url"] = base_url
    providers: dict[WorldFamily, Any] = llm_world_providers(OpenAI(**options))
    providers[WorldFamily.G5_HUMAN_PATTERNS] = human_provider
    return aggregate, aggregate_commitment, providers


def _assert_development(arguments: argparse.Namespace, repository_root: Path) -> None:
    if not arguments.development_output or not arguments.development_ledger:
        raise RuntimeError("generalization_r3_development_evidence_required")
    output_path = _private_source(Path(arguments.development_output))
    ledger_path = _private_source(Path(arguments.development_ledger))
    output = json.loads(output_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    results = output.get("results")
    expected_families = ["G1", "G2", "G3", "G4", "G5", "G6"]
    actual_families = [item.get("family") for item in results] if isinstance(results, list) else []
    result_passes = isinstance(results, list) and len(results) == 6 and all(item.get("passed") is True for item in results)
    valid = all((
        output.get("protocol_key") == "aporia_generalization_r3",
        output.get("phase") == "development",
        output.get("eligible") is True,
        output.get("first_failed_gate") is None,
        output.get("hidden_family") == "G8",
        output.get("personal_data_exported") is False,
        output.get("production_eligible") is False,
        actual_families == expected_families,
        result_passes,
        ledger.get("protocol_key") == "aporia_generalization_r3",
        ledger.get("phase") == "development",
        ledger.get("execution_count") == 1,
        ledger.get("environment") == "staging",
        ledger.get("eligible") is True,
        ledger.get("families") == expected_families,
        ledger.get("code_commitment") == code_commitment(repository_root),
        ledger.get("aggregate_source_commitment") == output.get("aggregate_source_commitment"),
        ledger.get("output_sha256") == hashlib.sha256(output_path.read_bytes()).hexdigest(),
        isinstance(ledger.get("reservation_inode"), int),
    ))
    if not valid:
        raise RuntimeError("generalization_r3_development_evidence_invalid")


def _assert_paths_fresh(output: Path, ledger: Path) -> None:
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
        raise RuntimeError("generalization_r3_execution_already_exists")
    if output.parent != ledger.parent:
        raise RuntimeError("generalization_r3_execution_storage_mismatch")


def _reserve(ledger: Path) -> int:
    descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        return os.fstat(descriptor).st_ino
    finally:
        os.close(descriptor)


def _private_source(path: Path) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 2097152:
        raise RuntimeError("generalization_r3_source_unavailable")
    if resolved.stat().st_mode & 0o077:
        raise RuntimeError("generalization_r3_source_permissions_invalid")
    return resolved


def _credentials(path: Path | None) -> tuple[str, str]:
    values = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "").strip(),
    }
    if path is not None:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 131072:
            raise RuntimeError("generalization_r3_env_unavailable")
        for line in resolved.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            name = key.strip()
            if separator and name in values and not values[name]:
                value = raw.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[name] = value
    return values["OPENAI_API_KEY"], values["OPENAI_BASE_URL"]


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
