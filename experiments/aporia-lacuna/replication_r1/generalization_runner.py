from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .generalization import WorldFamily, run_population
from .generalization_providers import AggregateHumanPatternProvider, llm_providers
from .sealing import assert_current_seal


DEVELOPMENT_SEEDS = tuple(range(30001, 30033))
CONFIRMATION_SEEDS = tuple(range(31001, 31033))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("development", "confirmation"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execution-ledger", required=True)
    parser.add_argument("--aggregate-patterns")
    parser.add_argument("--development-ledger")
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    assert_current_seal(repository_root)
    output = Path(arguments.output).resolve()
    ledger = Path(arguments.execution_ledger).resolve()
    _assert_fresh(output, ledger)
    if arguments.phase == "development":
        if not arguments.aggregate_patterns:
            raise RuntimeError("generalization_aggregate_patterns_required")
        api_key, base_url = _credentials(Path(arguments.env_file) if arguments.env_file else None)
        if not api_key:
            raise RuntimeError("generalization_openai_key_required")
        from openai import OpenAI

        options = {"api_key": api_key, "timeout": 90.0, "max_retries": 2}
        if base_url:
            options["base_url"] = base_url
        providers = llm_providers(OpenAI(**options))
        providers[WorldFamily.G5_HUMAN_PATTERNS] = AggregateHumanPatternProvider(Path(arguments.aggregate_patterns))
        results = run_population(DEVELOPMENT_SEEDS, WorldFamily.G8_ASYMMETRIC, False, providers)
    else:
        if not arguments.development_ledger:
            raise RuntimeError("generalization_development_ledger_required")
        development = json.loads(Path(arguments.development_ledger).read_text(encoding="utf-8"))
        if development.get("phase") != "development" or development.get("execution_count") != 1:
            raise RuntimeError("generalization_development_ledger_invalid")
        results = run_population(CONFIRMATION_SEEDS, WorldFamily.G8_ASYMMETRIC, True)
    payload = {
        "phase": arguments.phase,
        "analysis_unit": "seed_block",
        "independent_blocks": len(DEVELOPMENT_SEEDS if arguments.phase == "development" else CONFIRMATION_SEEDS),
        "results": [item.__dict__ for item in results],
        "hidden_family": WorldFamily.G8_ASYMMETRIC.value,
        "personal_data_exported": False,
        "production_eligible": False,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    output.write_text(rendered, encoding="utf-8")
    os.chmod(output, 0o600)
    receipt = {
        "phase": arguments.phase,
        "execution_count": 1,
        "environment": "staging",
        "output_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
    }
    ledger.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(ledger, 0o600)
    return 0


def _assert_fresh(output: Path, ledger: Path) -> None:
    if output.exists() or output.is_symlink() or ledger.exists() or ledger.is_symlink():
        raise RuntimeError("generalization_execution_already_exists")
    if output.parent.resolve() != ledger.parent.resolve():
        raise RuntimeError("generalization_execution_storage_mismatch")


def _credentials(path: Path | None) -> tuple[str, str]:
    values = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "").strip(),
    }
    if path is not None:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 131072:
            raise RuntimeError("generalization_env_unavailable")
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
