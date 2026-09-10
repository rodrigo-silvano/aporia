from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/mechanistic_r6/__init__.py",
    "experiments/aporia-lacuna/mechanistic_r6/mechanisms.py",
    "experiments/aporia-lacuna/mechanistic_r6/decoders.py",
    "experiments/aporia-lacuna/mechanistic_r6/protocol.py",
    "experiments/aporia-lacuna/mechanistic_r6/runner.py",
    "experiments/aporia-lacuna/mechanistic_r6/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_mechanistic_r6.py",
    "experiments/aporia-lacuna/fixtures/aporia_mechanistic_r6.json",
    "experiments/aporia-lacuna/fixtures/aporia_request_hash_vectors_r1.json",
    "services/agent-runtime/hosted/request_integrity.py",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or repository_root.resolve() not in resolved.parents:
            raise RuntimeError("mechanistic_r6_sealed_path_invalid")
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path, expected_commitment: str | None = None) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock_path = root / "aporia_mechanistic_r6.lock.json"
    preregistration = root / "aporia_mechanistic_r6.json"
    if lock_path.is_symlink() or preregistration.is_symlink():
        raise RuntimeError("mechanistic_r6_seal_path_invalid")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("status") != "sealed":
        raise RuntimeError("mechanistic_r6_not_sealed")
    current_commitment = code_commitment(repository_root)
    if lock.get("code_commitment") != current_commitment:
        raise RuntimeError("mechanistic_r6_code_commitment_mismatch")
    if expected_commitment is not None and expected_commitment != current_commitment:
        raise RuntimeError("mechanistic_r6_external_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("mechanistic_r6_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("mechanistic_r6_file_count_mismatch")
