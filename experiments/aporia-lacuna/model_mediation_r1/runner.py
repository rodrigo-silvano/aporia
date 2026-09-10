from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .protocol import LunaAlternativesProvider, run_suite
from .sealing import assert_current_seal, code_commitment


CONFIRMATION_SEEDS = tuple(range(72001, 72065))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--execution-ledger", required=True)
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeError("model_mediation_r1_staging_required")
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink() or output.parent != ledger.parent:
        raise RuntimeError("model_mediation_r1_already_executed")
    api_key, base_url = _credentials(Path(arguments.env_file) if arguments.env_file else None)
    if not api_key:
        raise RuntimeError("model_mediation_r1_openai_key_required")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    reservation_inode = os.fstat(descriptor).st_ino
    os.close(descriptor)
    from openai import OpenAI

    options: dict[str, object] = {"api_key": api_key, "timeout": 90.0, "max_retries": 2}
    if base_url:
        options["base_url"] = base_url
    provider = LunaAlternativesProvider(OpenAI(**options))
    result = run_suite(CONFIRMATION_SEEDS, provider)
    payload = {
        "protocol_key": "aporia_model_mediation_r1",
        "phase": "confirmation",
        "analysis_unit": "seed_block",
        "result": result.__dict__,
        "eligible": result.passed,
        "first_failed_gate": None if result.passed else "model_mediated_text_free_identity",
        "production_eligible": False,
        "scientific_claim": "model_mediated_text_free_structural_identity_effect" if result.passed else "model_mediated_effect_not_demonstrated",
        "phenomenology_established": False,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "protocol_key": "aporia_model_mediation_r1",
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "code_commitment": code_commitment(repository_root),
        "eligible": result.passed,
        "reservation_inode": reservation_inode,
    }
    ledger.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0 if result.passed else 1


def _credentials(path: Path | None) -> tuple[str, str]:
    values = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "").strip(),
    }
    if path is not None:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 131072:
            raise RuntimeError("model_mediation_r1_env_unavailable")
        for line in resolved.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            name = key.strip()
            if separator and name in values and not values[name]:
                value = raw.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[name] = value
    return values["OPENAI_API_KEY"], values["OPENAI_BASE_URL"]


if __name__ == "__main__":
    raise SystemExit(main())
