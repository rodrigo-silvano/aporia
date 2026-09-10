from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/post_r1/safe_agents.py",
    "experiments/aporia-lacuna/post_r1/safety.py",
    "experiments/aporia-lacuna/post_r1/runner.py",
    "experiments/aporia-lacuna/tests/test_aporia_post_r1_safety.py",
    "experiments/aporia-lacuna/fixtures/aporia_post_r1_safety_v1.json",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        if not path.is_file():
            raise RuntimeError("post_r1_safety_sealed_path_missing")
        relative_bytes = relative.encode()
        content = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_post_r1_safety_v1.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_post_r1_safety_v1.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("post_r1_safety_not_sealed")
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("post_r1_safety_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("post_r1_safety_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("post_r1_safety_file_count_mismatch")
