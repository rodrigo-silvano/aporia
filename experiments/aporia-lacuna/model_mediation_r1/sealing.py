from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/model_mediation_r1/__init__.py",
    "experiments/aporia-lacuna/model_mediation_r1/protocol.py",
    "experiments/aporia-lacuna/model_mediation_r1/runner.py",
    "experiments/aporia-lacuna/model_mediation_r1/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_model_mediation_r1.py",
    "experiments/aporia-lacuna/fixtures/aporia_model_mediation_r1.json",
    "experiments/aporia-lacuna/generalization_r3/mechanisms.py",
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
            raise RuntimeError("model_mediation_r1_sealed_path_missing")
        name = relative.encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock = json.loads((root / "aporia_model_mediation_r1.lock.json").read_text(encoding="utf-8"))
    preregistration = root / "aporia_model_mediation_r1.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("model_mediation_r1_not_sealed")
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("model_mediation_r1_code_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("model_mediation_r1_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("model_mediation_r1_file_count_mismatch")
