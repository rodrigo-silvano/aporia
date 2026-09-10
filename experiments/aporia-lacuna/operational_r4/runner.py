from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from .protocol import CONFIRMATION_SEEDS, DEVELOPMENT_SEEDS, report_dict, run_protocol
from .sealing import assert_current_seal, code_commitment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirmatory"), default="validate")
    parser.add_argument("--output")
    parser.add_argument("--execution-ledger")
    parser.add_argument("--expected-commitment")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    assert_current_seal(root)
    if arguments.mode == "validate":
        print(_render(_payload(report_dict(run_protocol("development", DEVELOPMENT_SEEDS)))), end="")
        return 0
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("operational_r4_staging_required")
    if not arguments.output or not arguments.execution_ledger or not arguments.expected_commitment:
        raise RuntimeError("operational_r4_confirmatory_requirements_missing")
    assert_current_seal(root, arguments.expected_commitment)
    sealed_commitment = code_commitment(root)
    output, ledger = _confirmatory_paths(Path(arguments.output), Path(arguments.execution_ledger))
    ledger_descriptor = _reserve(ledger)
    output_descriptor = -1
    try:
        output_descriptor = _reserve(output)
        rendered = _render(_payload(report_dict(run_protocol("confirmation", CONFIRMATION_SEEDS))))
        _write_reserved(output_descriptor, rendered)
        receipt = {
            "protocol_key": "aporia_operational_r4",
            "execution_count": 1,
            "environment": "staging",
            "status": "completed",
            "rerun_allowed": False,
            "output_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "code_commitment": sealed_commitment,
            "external_commitment_verified": True,
            "production_eligible": False,
        }
        _write_reserved(ledger_descriptor, _render(receipt))
    except BaseException as exception:
        receipt = {
            "protocol_key": "aporia_operational_r4",
            "execution_count": 1,
            "environment": "staging",
            "status": "invalidated",
            "rerun_allowed": False,
            "error_class": exception.__class__.__name__,
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
    return {
        "protocol_key": "aporia_operational_r4",
        "extends_without_repeating": "aporia_operational_r3",
        "report": report,
        "eligible": report["passed"] is True,
        "production_eligible": False,
    }


def _confirmatory_paths(output: Path, ledger: Path) -> tuple[Path, Path]:
    output_parent = output.parent.resolve()
    ledger_parent = ledger.parent.resolve()
    if output_parent != ledger_parent:
        raise RuntimeError("operational_r4_confirmatory_storage_mismatch")
    output_parent.mkdir(parents=True, exist_ok=True)
    if not output_parent.is_dir():
        raise RuntimeError("operational_r4_confirmatory_storage_invalid")
    return output_parent / output.name, ledger_parent / ledger.name


def _reserve(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exception:
        raise RuntimeError("operational_r4_confirmatory_already_executed") from exception
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("operational_r4_confirmatory_storage_invalid")
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
