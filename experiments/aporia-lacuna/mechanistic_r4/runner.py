from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .protocol import (
    CONFIRMATION_SEEDS,
    CONFIRMATION_SYNTHETIC_SEEDS,
    DEVELOPMENT_SEEDS,
    DEVELOPMENT_SYNTHETIC_SEEDS,
    LunaProposalProvider,
    SyntheticProposalProvider,
    report_dict,
    run_protocol,
)
from .sealing import assert_current_seal, code_commitment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    if arguments.mode == "validate":
        report = run_protocol(
            "development",
            SyntheticProposalProvider(),
            DEVELOPMENT_SEEDS,
            DEVELOPMENT_SYNTHETIC_SEEDS,
        )
        payload = _payload(report_dict(report))
        rendered = _render(payload)
        if arguments.output:
            Path(arguments.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("mechanistic_r4_staging_required")
    if not arguments.output or not arguments.execution_ledger:
        raise RuntimeError("mechanistic_r4_confirmatory_paths_required")
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    _assert_fresh(output, ledger)
    api_key, base_url = _credentials(Path(arguments.env_file) if arguments.env_file else None)
    if not api_key:
        raise RuntimeError("mechanistic_r4_openai_key_required")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    reservation_inode = os.fstat(descriptor).st_ino
    os.close(descriptor)
    from openai import OpenAI

    options: dict[str, object] = {"api_key": api_key, "timeout": 120.0, "max_retries": 2}
    if base_url:
        options["base_url"] = base_url
    provider = LunaProposalProvider(OpenAI(**options))
    try:
        report = run_protocol(
            "confirmation",
            provider,
            CONFIRMATION_SEEDS,
            CONFIRMATION_SYNTHETIC_SEEDS,
        )
    except BaseException as exception:
        receipt = {
            "protocol_key": "aporia_mechanistic_r4",
            "execution_count": 1,
            "environment": "staging",
            "status": "invalidated",
            "rerun_allowed": False,
            "model_calls": len(provider.calls),
            "reason_code": "technical_incompatibility_during_execution",
            "error_class": exception.__class__.__name__,
            "reservation_inode": reservation_inode,
            "code_commitment": code_commitment(repository_root),
        }
        ledger.write_text(_render(receipt), encoding="utf-8")
        os.chmod(ledger, 0o600)
        raise
    payload = _payload(report_dict(report))
    rendered = _render(payload)
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_mechanistic_r4",
        "execution_count": 1,
        "environment": "staging",
        "status": "completed",
        "rerun_allowed": False,
        "model_calls": len(provider.calls),
        "output_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "code_commitment": code_commitment(repository_root),
        "reservation_inode": reservation_inode,
        "production_eligible": False,
    }
    ledger.write_text(_render(receipt), encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0


def _payload(report: dict[str, object]) -> dict[str, object]:
    first_failed_gate = None
    if report["mechanistic_localization"] != "PASS":
        first_failed_gate = "mechanistic_localization"
    elif report["information_parity_state"] != "PASS":
        first_failed_gate = "information_parity"
    elif report["provenance_dependence"] != "PASS":
        first_failed_gate = "provenance_dependence"
    elif report["longitudinal_poisoning"] != "PASS":
        first_failed_gate = "longitudinal_poisoning"
    return {
        "protocol_key": "aporia_mechanistic_r4",
        "phase": report["phase"],
        "analysis_unit": "seed_block",
        "report": report,
        "technical_execution_complete": True,
        "eligible": all((
            report["mechanistic_localization"] == "PASS",
            report["information_parity_state"] == "PASS",
            report["provenance_dependence"] == "PASS",
            report["longitudinal_poisoning"] == "PASS",
        )),
        "first_failed_gate": first_failed_gate,
        "scientific_claim": "evidence_compatible_with_operational_functional_properties",
        "production_eligible": False,
        "phenomenology_established": False,
    }


def _credentials(path: Path | None) -> tuple[str, str]:
    values = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "").strip(),
    }
    if path is not None:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 131072:
            raise RuntimeError("mechanistic_r4_env_unavailable")
        for line in resolved.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            name = key.strip()
            if separator and name in values and not values[name]:
                value = raw.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[name] = value
    return values["OPENAI_API_KEY"], values["OPENAI_BASE_URL"]


def _assert_fresh(output: Path, ledger: Path) -> None:
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
        raise RuntimeError("mechanistic_r4_confirmatory_already_executed")
    if output.parent != ledger.parent:
        raise RuntimeError("mechanistic_r4_confirmatory_storage_mismatch")


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
