from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

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


CONFIRMATORY_MAX_RETRIES = 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    parser.add_argument("--env-file")
    parser.add_argument("--expected-commitment")
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
        rendered = _render(_payload(report_dict(report)))
        if arguments.output:
            descriptor = _reserve(Path(arguments.output))
            try:
                _write_reserved(descriptor, rendered)
            finally:
                os.close(descriptor)
        else:
            print(rendered, end="")
        return 0
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("mechanistic_r6_staging_required")
    if not arguments.output or not arguments.execution_ledger or not arguments.expected_commitment:
        raise RuntimeError("mechanistic_r6_confirmatory_requirements_missing")
    assert_current_seal(repository_root, arguments.expected_commitment)
    sealed_commitment = code_commitment(repository_root)
    output, ledger = _confirmatory_paths(Path(arguments.output), Path(arguments.execution_ledger))
    api_key, base_url = _credentials(Path(arguments.env_file) if arguments.env_file else None)
    if not api_key:
        raise RuntimeError("mechanistic_r6_openai_key_required")
    validated_base_url = _validated_base_url(base_url)
    from openai import OpenAI

    options: dict[str, object] = {
        "api_key": api_key,
        "timeout": 120.0,
        "max_retries": CONFIRMATORY_MAX_RETRIES,
    }
    if validated_base_url:
        options["base_url"] = validated_base_url
    provider = LunaProposalProvider(OpenAI(**options))
    ledger_descriptor = _reserve(ledger)
    output_descriptor = -1
    reservation_inode = os.fstat(ledger_descriptor).st_ino
    try:
        output_descriptor = _reserve(output)
        report = run_protocol(
            "confirmation",
            provider,
            CONFIRMATION_SEEDS,
            CONFIRMATION_SYNTHETIC_SEEDS,
        )
        rendered = _render(_payload(report_dict(report)))
        _write_reserved(output_descriptor, rendered)
        receipt = {
            "protocol_key": "aporia_mechanistic_r6",
            "execution_count": 1,
            "environment": "staging",
            "status": "completed",
            "rerun_allowed": False,
            "model_calls": len(provider.calls),
            "output_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "code_commitment": sealed_commitment,
            "external_commitment_verified": True,
            "reservation_inode": reservation_inode,
            "production_eligible": False,
        }
        _write_reserved(ledger_descriptor, _render(receipt))
    except BaseException as exception:
        receipt = {
            "protocol_key": "aporia_mechanistic_r6",
            "execution_count": 1,
            "environment": "staging",
            "status": "invalidated",
            "rerun_allowed": False,
            "model_calls": len(provider.calls),
            "reason_code": "technical_incompatibility_during_execution",
            "error_class": exception.__class__.__name__,
            "reservation_inode": reservation_inode,
            "code_commitment": sealed_commitment,
            "external_commitment_verified": True,
        }
        _write_reserved(ledger_descriptor, _render(receipt))
        raise
    finally:
        if output_descriptor >= 0:
            os.close(output_descriptor)
        os.close(ledger_descriptor)
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
        "protocol_key": "aporia_mechanistic_r6",
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
            raise RuntimeError("mechanistic_r6_env_unavailable")
        for line in resolved.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            name = key.strip()
            if separator and name in values and not values[name]:
                value = raw.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[name] = value
    return values["OPENAI_API_KEY"], values["OPENAI_BASE_URL"]


def _validated_base_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if any((parsed.scheme != "https", parsed.hostname != "api.openai.com", parsed.username, parsed.password, parsed.query, parsed.fragment)):
        raise RuntimeError("mechanistic_r6_base_url_invalid")
    if parsed.port not in {None, 443} or parsed.path.rstrip("/") not in {"", "/v1"}:
        raise RuntimeError("mechanistic_r6_base_url_invalid")
    return value


def _confirmatory_paths(output: Path, ledger: Path) -> tuple[Path, Path]:
    output_parent = output.parent.resolve()
    ledger_parent = ledger.parent.resolve()
    if output_parent != ledger_parent:
        raise RuntimeError("mechanistic_r6_confirmatory_storage_mismatch")
    output_parent.mkdir(parents=True, exist_ok=True)
    if output_parent.is_symlink() or not output_parent.is_dir():
        raise RuntimeError("mechanistic_r6_confirmatory_storage_invalid")
    return output_parent / output.name, ledger_parent / ledger.name


def _reserve(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exception:
        raise RuntimeError("mechanistic_r6_confirmatory_already_executed") from exception
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("mechanistic_r6_confirmatory_storage_invalid")
    return descriptor


def _write_reserved(descriptor: int, value: str) -> None:
    content = value.encode("utf-8")
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    offset = 0
    while offset < len(content):
        offset += os.write(descriptor, content[offset:])
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)


def _render(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
