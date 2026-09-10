from __future__ import annotations

import hashlib
import json
from pathlib import Path


RELATIVE_PATHS = (
    "experiments/aporia-lacuna/mechanistic_r7/__init__.py",
    "experiments/aporia-lacuna/mechanistic_r7/canonical_state.py",
    "experiments/aporia-lacuna/mechanistic_r7/operators.py",
    "experiments/aporia-lacuna/mechanistic_r7/protocol.py",
    "experiments/aporia-lacuna/mechanistic_r7/runner.py",
    "experiments/aporia-lacuna/mechanistic_r7/sealing.py",
    "experiments/aporia-lacuna/tests/test_aporia_mechanistic_r7.py",
    "experiments/aporia-lacuna/fixtures/aporia_mechanistic_r7.json",
    "services/agent-runtime/hosted/request_integrity.py",
    "services/agent-runtime/gateway/main.py",
    "apps/authenticated-web/controllers/Api/Internal/AssistantRuntimeToolApiEndpoint.php",
    "core/Infrastructure/Persistence/AporiaEcologicalTransportPrecondition.php",
    "core/Infrastructure/Persistence/AporiaProspectiveEcologicalProgram.php",
    "platform/database/migrations/2026_08_24_aporia_ecological_transport_precondition_r1.sql",
)


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in RELATIVE_PATHS:
        path = repository_root / relative
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or repository_root.resolve() not in resolved.parents:
            raise RuntimeError("mechanistic_r7_sealed_path_invalid")
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path, expected_commitment: str | None = None) -> None:
    root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock_path = root / "aporia_mechanistic_r7.lock.json"
    preregistration = root / "aporia_mechanistic_r7.json"
    if lock_path.is_symlink() or preregistration.is_symlink():
        raise RuntimeError("mechanistic_r7_seal_path_invalid")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("status") != "sealed":
        raise RuntimeError("mechanistic_r7_not_sealed")
    current_commitment = code_commitment(repository_root)
    if lock.get("code_commitment") != current_commitment:
        raise RuntimeError("mechanistic_r7_code_commitment_mismatch")
    if expected_commitment is not None and expected_commitment != current_commitment:
        raise RuntimeError("mechanistic_r7_external_commitment_mismatch")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("mechanistic_r7_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(RELATIVE_PATHS):
        raise RuntimeError("mechanistic_r7_file_count_mismatch")
