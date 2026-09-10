from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from longitudinal.sealing import assert_current_seal as assert_longitudinal_seal
from longitudinal.sealing import sealed_paths as longitudinal_sealed_paths
from mechanistic_r4.sealing import assert_current_seal as assert_mechanistic_seal
from model_mediation_r1.sealing import assert_current_seal as assert_mediation_seal
from operational_r2.sealing import assert_current_seal as assert_operational_r2_seal
from operational_r3.sealing import assert_current_seal as assert_operational_r3_seal
from replication_r1.archive import assert_v3_archive


def build_inventory(repository_root: Path) -> dict[str, object]:
    fixture_path = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_artifact_freeze_r4.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    v3_manifest = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_v3_archive_manifest.json"
    v3 = assert_v3_archive(v3_manifest)
    assert_longitudinal_seal(repository_root)
    assert_mediation_seal(repository_root)
    assert_operational_r2_seal(repository_root)
    assert_mechanistic_seal(repository_root)
    assert_operational_r3_seal(repository_root)
    if len(longitudinal_sealed_paths(repository_root)) != 24:
        raise RuntimeError("aporia_artifact_freeze_v3_file_count_mismatch")
    if v3["confirmatory_seed_range"] != [4701, 4732]:
        raise RuntimeError("aporia_artifact_freeze_v3_seeds_mismatch")
    external = fixture["historical_artifacts"]
    if len(external) != 13 or any(item["execution_count"] != 1 for item in external):
        raise RuntimeError("aporia_artifact_freeze_execution_count_mismatch")
    if len({item["sha256"] for item in external}) != len(external):
        raise RuntimeError("aporia_artifact_freeze_hash_collision")
    paths = scientific_paths(repository_root)
    changed = changed_paths(repository_root)
    local = [metadata(repository_root, path, changed) for path in paths]
    return {
        "protocol_key": "aporia_artifact_freeze_r4",
        "initial_commit": fixture["initial_commit"],
        "initial_branch": fixture["initial_branch"],
        "initial_release": fixture["initial_release"],
        "v3_seed_range": fixture["v3_confirmatory_seed_range"],
        "v3_sealed_file_count": fixture["v3_sealed_file_count"],
        "g8_status": fixture["g8_status"],
        "historical_runtime_artifacts": external,
        "local_scientific_artifact_count": len(local),
        "local_scientific_artifacts": local,
        "r1_additional_executions": 0,
        "r2_additional_executions": 0,
        "verified": True,
    }


def scientific_paths(repository_root: Path) -> tuple[Path, ...]:
    candidates = []
    roots = [
        repository_root / "experiments" / "aporia-lacuna",
        repository_root / "core" / "Application" / "Assistant",
        repository_root / "core" / "Infrastructure" / "Persistence",
        repository_root / "platform" / "database" / "migrations",
        repository_root / "tests" / "Unit",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(repository_root).as_posix()
            if relative.startswith("experiments/aporia-lacuna/") or "Aporia" in path.name or "aporia" in path.name:
                candidates.append(path)
    return tuple(sorted(set(candidates), key=lambda item: item.relative_to(repository_root).as_posix()))


def metadata(repository_root: Path, path: Path, changed: set[str]) -> dict[str, object]:
    content = path.read_bytes()
    relative = path.relative_to(repository_root).as_posix()
    stat = path.stat()
    return {
        "path": relative,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "git_blob_hash": hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "experiment_id": experiment_id(relative),
        "preregistration_version": preregistration_version(relative),
        "seed_set": seed_set(relative),
        "execution_count": 0,
        "release": "working_tree_before_staging_release",
        "commit": "working_tree_before_final_commit" if relative in changed else last_commit(repository_root, relative),
        "status": "working_tree_new_or_modified" if relative in changed else "tracked_preserved",
    }


def changed_paths(repository_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return {line[3:] for line in result.stdout.splitlines() if len(line) > 3}


def last_commit(repository_root: Path, relative: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", relative],
        cwd=repository_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip() or "UNTRACKED"


def experiment_id(relative: str) -> str:
    parts = relative.split("/")
    if "aporia-lacuna" in parts:
        index = parts.index("aporia-lacuna")
        if len(parts) > index + 1 and parts[index + 1] not in {"fixtures", "tests", "protocols", "sidecar"}:
            return "aporia_" + parts[index + 1]
    return "aporia_runtime_foundation"


def preregistration_version(relative: str) -> str:
    for version in ("r4", "r3", "r2", "r1", "v6", "v5", "v4", "v3", "v2", "v1"):
        if version in relative.lower():
            return version
    return "foundation"


def seed_set(relative: str) -> str:
    if "longitudinal" in relative and "v3" in relative:
        return "4701-4732"
    if "mechanistic_r4" in relative:
        return "development:85001-85048,87001-87128;confirmation:86001-86048,88001-88128"
    if "operational_r3" in relative:
        return "development:89001-89256;confirmation:89301-89556"
    return "defined_in_preregistration_or_not_applicable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    inventory = build_inventory(root)
    print(json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=None if arguments.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
