from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/operational_r3/__init__.py",
    "experiments/aporia-lacuna/operational_r3/lifecycle.py",
    "experiments/aporia-lacuna/operational_r3/protocol.py",
    "experiments/aporia-lacuna/operational_r3/runner.py",
    "experiments/aporia-lacuna/operational_r3/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_operational_r3.py",
    "experiments/aporia-lacuna/fixtures/aporia_operational_r3.json",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_operational_r3.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_operational_r3.json"
    if lock.get("status") != "sealed" or lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("operational_r3_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("operational_r3_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("operational_r3_file_count_mismatch")
