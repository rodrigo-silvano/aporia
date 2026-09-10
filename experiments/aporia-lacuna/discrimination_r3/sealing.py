from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/discrimination_r3/__init__.py",
    "experiments/aporia-lacuna/discrimination_r3/mechanisms.py",
    "experiments/aporia-lacuna/discrimination_r3/protocols.py",
    "experiments/aporia-lacuna/discrimination_r3/runner.py",
    "experiments/aporia-lacuna/discrimination_r3/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_discrimination_r3.py",
    "experiments/aporia-lacuna/fixtures/aporia_discrimination_r3.json",
    "experiments/aporia-lacuna/longitudinal/agents.py",
    "experiments/aporia-lacuna/longitudinal/contracts.py",
    "experiments/aporia-lacuna/post_r1/safe_agents.py",
    "experiments/aporia-lacuna/replication_r1/statistics.py",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        if not path.is_file():
            raise RuntimeError("discrimination_r3_sealed_path_missing")
        name = relative.encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_discrimination_r3.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_discrimination_r3.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("discrimination_r3_not_sealed")
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("discrimination_r3_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("discrimination_r3_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("discrimination_r3_file_count_mismatch")
