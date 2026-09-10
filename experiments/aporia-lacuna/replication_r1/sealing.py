from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def sealed_paths(repository_root: Path) -> tuple[Path, ...]:
    experiment = repository_root / "experiments" / "aporia-lacuna"
    paths = list((experiment / "replication_r1").glob("*.py"))
    paths.extend([
        experiment / "tests" / "test_aporia_generalization_r1.py",
        experiment / "tests" / "test_aporia_lifecycle_r1.py",
        experiment / "tests" / "test_aporia_replication_r1.py",
    ])
    paths.extend([
        experiment / "fixtures" / "aporia_replication_r1.json",
        experiment / "fixtures" / "aporia_v3_archive_manifest.json",
        repository_root / "core" / "Infrastructure" / "Persistence" / "AporiaLongitudinalShadowTwinsR1.php",
        repository_root / "core" / "Infrastructure" / "Persistence" / "AporiaShadowLifecycleLedger.php",
        repository_root / "core" / "Application" / "Assistant" / "AporiaAdvisoryContextService.php",
        repository_root / "core" / "Application" / "Assistant" / "AssistantRuntimeSessionService.php",
        repository_root / "core" / "Application" / "Support" / "Runtime" / "Account" / "AccountErasureService.php",
        repository_root / "core" / "Application" / "Support" / "Runtime" / "Worker" / "RuntimeWorkerSupport.php",
        repository_root / "apps" / "authenticated-web" / "controllers" / "Api" / "Internal" / "AssistantRuntimeToolApiEndpoint.php",
        repository_root / "platform" / "database" / "migrations" / "2026_08_22_aporia_shadow_lifecycle_r1.sql",
        repository_root / "tests" / "Unit" / "Application" / "Assistant" / "AporiaRealPilotTest.php",
        repository_root / "tests" / "Unit" / "Architecture" / "ActionAuditCoverageTest.php",
        repository_root / "tests" / "Unit" / "Assistant" / "AssistantRuntimeSessionServiceTest.php",
        repository_root / "tests" / "Unit" / "Infrastructure" / "Persistence" / "AporiaShadowLifecycleLedgerTest.php",
    ])
    return tuple(sorted(paths, key=lambda path: path.relative_to(repository_root).as_posix()))


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sealed_paths(repository_root):
        if not path.is_file():
            raise RuntimeError("replication_sealed_path_missing")
        relative = path.relative_to(repository_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    experiment = repository_root / "experiments" / "aporia-lacuna"
    lock = json.loads((experiment / "fixtures" / "aporia_replication_r1.lock.json").read_text(encoding="utf-8"))
    preregistration = experiment / "fixtures" / "aporia_replication_r1.json"
    if lock.get("status") != "sealed":
        raise RuntimeError("replication_not_sealed")
    if lock.get("preregistration_sha256") != hashlib.sha256(preregistration.read_bytes()).hexdigest():
        raise RuntimeError("replication_preregistration_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(sealed_paths(repository_root)):
        raise RuntimeError("replication_sealed_file_count_mismatch")
    result_path = experiment / "fixtures" / "aporia_replication_r1_result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        valid_archive = all([
            result.get("status") == "executed_failed",
            result.get("execution_environment") == "staging",
            result.get("execution_count") == 1,
            result.get("source_commit") == "f5ac0efe5",
            result.get("protocol_key") == "aporia_independent_replication_r1",
            result.get("first_failed_gate") == "zero_critical_unsafe_commits",
            result.get("eligible") is False,
            result.get("production_eligible") is False,
            re.fullmatch(r"[0-9a-f]{64}", str(result.get("output_sha256") or "")) is not None,
            re.fullmatch(r"[0-9a-f]{64}", str(result.get("execution_ledger_sha256") or "")) is not None,
            re.fullmatch(r"[0-9a-f]{64}", str(result.get("design_commitment") or "")) is not None,
        ])
        if not valid_archive:
            raise RuntimeError("replication_executed_archive_invalid")
        return
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("replication_code_commitment_mismatch")
