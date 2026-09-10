from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/generalization_r2/__init__.py",
    "experiments/aporia-lacuna/generalization_r2/contracts.py",
    "experiments/aporia-lacuna/generalization_r2/providers.py",
    "experiments/aporia-lacuna/generalization_r2/experiment.py",
    "experiments/aporia-lacuna/generalization_r2/runner.py",
    "experiments/aporia-lacuna/generalization_r2/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_generalization_r2.py",
    "experiments/aporia-lacuna/fixtures/aporia_generalization_r2.json",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        if not path.is_file():
            raise RuntimeError("generalization_r2_sealed_path_missing")
        relative_bytes = relative.encode()
        content = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_generalization_r2.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_generalization_r2.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("generalization_r2_not_sealed")
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("generalization_r2_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("generalization_r2_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("generalization_r2_file_count_mismatch")
