from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/mechanistic_r4/__init__.py",
    "experiments/aporia-lacuna/mechanistic_r4/mechanisms.py",
    "experiments/aporia-lacuna/mechanistic_r4/decoders.py",
    "experiments/aporia-lacuna/mechanistic_r4/protocol.py",
    "experiments/aporia-lacuna/mechanistic_r4/runner.py",
    "experiments/aporia-lacuna/mechanistic_r4/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_mechanistic_r4.py",
    "experiments/aporia-lacuna/fixtures/aporia_mechanistic_r4.json",
    "experiments/aporia-lacuna/fixtures/aporia_request_hash_vectors_r1.json",
    "services/agent-runtime/hosted/request_integrity.py",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        if not path.is_file():
            raise RuntimeError("mechanistic_r4_sealed_path_missing")
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_mechanistic_r4.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_mechanistic_r4.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("mechanistic_r4_not_sealed")
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("mechanistic_r4_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("mechanistic_r4_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("mechanistic_r4_file_count_mismatch")
