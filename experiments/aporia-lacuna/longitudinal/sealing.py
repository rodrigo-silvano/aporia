from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


def sealed_paths(repository_root: Path) -> tuple[Path, ...]:
    experiment = repository_root / "experiments" / "aporia-lacuna"
    values = list((experiment / "longitudinal").glob("*.py"))
    values.extend((experiment / "tests").glob("test_aporia_longitudinal_*.py"))
    values.extend([
        experiment / "fixtures" / "aporia_c0_c5_v6.lock.json",
        experiment / "fixtures" / "aporia_longitudinal_holdout_v1.lock.json",
        experiment / "fixtures" / "aporia_longitudinal_prereg_v3.json",
        repository_root / "core" / "Infrastructure" / "Persistence" / "AporiaLongitudinalShadowTwins.php",
        repository_root / "apps" / "authenticated-web" / "controllers" / "Api" / "Internal" / "AssistantRuntimeToolApiEndpoint.php",
        repository_root / "core" / "Application" / "Support" / "Runtime" / "Account" / "AccountErasureService.php",
        repository_root / "platform" / "database" / "migrations" / "2026_08_22_aporia_longitudinal_shadow_twins.sql",
        repository_root / "tests" / "Unit" / "Infrastructure" / "Persistence" / "AporiaLongitudinalShadowTwinsTest.php",
    ])
    return tuple(sorted(values, key=lambda path: path.relative_to(repository_root).as_posix()))


def code_commitment(repository_root: Path) -> str:
    manifest_path = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_v3_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_commit = str(manifest.get("source_commit") or "")
    if re.fullmatch(r"[0-9a-f]{7,40}", source_commit) is None:
        raise RuntimeError("sealed_source_commit_invalid")
    digest = hashlib.sha256()
    for path in sealed_paths(repository_root):
        if not path.is_file():
            raise RuntimeError("sealed_code_path_missing")
        relative_text = path.relative_to(repository_root).as_posix()
        relative = relative_text.encode()
        result = subprocess.run(
            ["git", "show", f"{source_commit}:{relative_text}"],
            cwd=repository_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("sealed_source_file_unavailable")
        content = result.stdout
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path) -> None:
    lock_path = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_longitudinal_prereg_v3.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("code_commitment") != code_commitment(repository_root):
        raise RuntimeError("sealed_code_commitment_mismatch")
