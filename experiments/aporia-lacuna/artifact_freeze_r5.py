from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from longitudinal.sealing import assert_current_seal as assert_longitudinal_seal
from longitudinal.sealing import sealed_paths as longitudinal_sealed_paths
from mechanistic_r5.sealing import assert_current_seal as assert_mechanistic_seal
from model_mediation_r1.sealing import assert_current_seal as assert_mediation_seal
from operational_r2.sealing import assert_current_seal as assert_operational_r2_seal
from operational_r4.sealing import assert_current_seal as assert_operational_r4_seal
from replication_r1.archive import assert_v3_archive


EXPECTED_INITIAL = (
    "86a3a17ad21a754e573f5f9995566a403b943eec",
    "staging",
    "deploy-20260822-staging-get-pricing-route-v1",
)
EXPECTED_HISTORICAL_SHA256 = "de3f51e9f80074c3a8aa8c3ee6f359855de507c3d105d1b31eb724640bf2d47e"
EXPECTED_HISTORICAL = (
    ("runtime/storage/aporia-r1/discrimination-r2-ledger.json", 272, "0600", "aa45a10c7da4ff190625d4059c7420a5bba91ae1e4db658ba0bdce9062ce2030", 1),
    ("runtime/storage/aporia-r1/discrimination-r2-result.json", 1552, "0600", "41b0c61d06729710dcc7f5cddd0b6b18916660a6dd3dea92394810ea5039b5af", 1),
    ("runtime/storage/aporia-r1/discrimination-r3-ledger.json", 272, "0600", "fd13aa7cc7f0219b046fbaa6637fdf1047d70593b3bdff330a65ab3f8a7e6b12", 1),
    ("runtime/storage/aporia-r1/discrimination-r3-result.json", 2117, "0600", "43c7a935a05b564850af53360688fc38556de536412a01c9137d09148781c84d", 1),
    ("runtime/storage/aporia-r1/model-mediation-r1-ledger.json", 301, "0600", "cfc1f7934ede80b5abf21d3aacad50f0c9b0b96e5ae3031645e323124865398c", 1),
    ("runtime/storage/aporia-r1/model-mediation-r1-result.json", 916, "0600", "87555a38d0d4fcb4901b785bebb5ffad5f8fc2f3f175637c5b96a77364f22e72", 1),
    ("runtime/storage/aporia-r1/operational-r2-ledger.json", 281, "0600", "27311b4908b29457a4b857122eaeb943e6d46af03079310bb1f376603b454d28", 1),
    ("runtime/storage/aporia-r1/operational-r2-result.json", 1196, "0600", "6aa5bd67b3a046b25061cb3f3a1d600113a5c334ff08896497c8383d3125237d", 1),
    ("runtime/storage/aporia-r1/post-r1-safety-v1-ledger.json", 171, "0600", "615fde7d9961408985d9ddd3167a793dd9c0aceff02e2f211a6b22746daf7a80", 1),
    ("runtime/storage/aporia-r1/post-r1-safety-v1-result.json", 474, "0600", "caef931b89400d5111d8cd1b7c201a48155fc8f2739ed84bdde7bd8c3e95c33a", 1),
    ("runtime/storage/aporia-r1/replication-r1-execution-ledger.json", 267, "0600", "651632ead6d64bb9088247825e80b3e40bad587208e4f36b5bb660cd7bcc5174", 1),
    ("runtime/storage/aporia-r1/replication-r1-output.json", 9803, "0600", "0d0c9b2614635f7ac1cbd870141efd63d23c8f955d52da3e8fa0f5148602c69d", 1),
    ("runtime/storage/aporia/aporia-longitudinal-confirmatory-v3.json", 63936, "0600", "a9a89c1f39aff3cced6eca1af8eaa80a17787e90657678aa95dae907199801d2", 1),
)
FREEZE_RELATIVE_PATHS = (
    "experiments/aporia-lacuna/artifact_freeze_r5.py",
    "experiments/aporia-lacuna/tests/test_aporia_artifact_freeze_r5.py",
    "experiments/aporia-lacuna/fixtures/aporia_artifact_freeze_r5.json",
    "experiments/aporia-lacuna/fixtures/aporia_mechanistic_r5.lock.json",
    "experiments/aporia-lacuna/fixtures/aporia_operational_r4.lock.json",
)


def build_inventory(repository_root: Path, expected_commitment: str | None = None) -> dict[str, object]:
    assert_current_seal(repository_root, expected_commitment)
    fixture_path = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_artifact_freeze_r5.json"
    if fixture_path.is_symlink():
        raise RuntimeError("aporia_artifact_freeze_r5_fixture_invalid")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    v3_manifest = repository_root / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_v3_archive_manifest.json"
    v3 = assert_v3_archive(v3_manifest)
    assert_longitudinal_seal(repository_root)
    assert_mediation_seal(repository_root)
    assert_operational_r2_seal(repository_root)
    assert_mechanistic_seal(repository_root)
    assert_operational_r4_seal(repository_root)
    if len(longitudinal_sealed_paths(repository_root)) != 24:
        raise RuntimeError("aporia_artifact_freeze_v3_file_count_mismatch")
    if v3["confirmatory_seed_range"] != [4701, 4732]:
        raise RuntimeError("aporia_artifact_freeze_v3_seeds_mismatch")
    external = fixture["historical_artifacts"]
    initial = (fixture.get("initial_commit"), fixture.get("initial_branch"), fixture.get("initial_release"))
    if initial != EXPECTED_INITIAL:
        raise RuntimeError("aporia_artifact_freeze_r5_initial_anchor_mismatch")
    historical = tuple(
        (item.get("path"), item.get("size"), item.get("mode"), item.get("sha256"), item.get("execution_count"))
        for item in external
        if isinstance(item, dict)
    )
    if historical != EXPECTED_HISTORICAL:
        raise RuntimeError("aporia_artifact_freeze_r5_historical_anchor_mismatch")
    historical_sha256 = hashlib.sha256(
        json.dumps(external, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if historical_sha256 != EXPECTED_HISTORICAL_SHA256:
        raise RuntimeError("aporia_artifact_freeze_r5_historical_manifest_mismatch")
    paths = scientific_paths(repository_root)
    changed = changed_paths(repository_root)
    local = [metadata(repository_root, path, changed) for path in paths]
    return {
        "protocol_key": "aporia_artifact_freeze_r5",
        "code_commitment": code_commitment(repository_root),
        "external_commitment_verified": expected_commitment is not None,
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
            if path.is_symlink():
                relative = path.relative_to(repository_root).as_posix()
                if relative.startswith("experiments/aporia-lacuna/") or "Aporia" in path.name or "aporia" in path.name:
                    raise RuntimeError("aporia_artifact_freeze_r5_symlink_rejected")
                continue
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            resolved = path.resolve()
            if repository_root.resolve() not in resolved.parents:
                raise RuntimeError("aporia_artifact_freeze_r5_path_escape")
            relative = path.relative_to(repository_root).as_posix()
            if relative.startswith("experiments/aporia-lacuna/") or "Aporia" in path.name or "aporia" in path.name:
                candidates.append(path)
    return tuple(sorted(set(candidates), key=lambda item: item.relative_to(repository_root).as_posix()))


def metadata(repository_root: Path, path: Path, changed: set[str]) -> dict[str, object]:
    if path.is_symlink() or repository_root.resolve() not in path.resolve().parents:
        raise RuntimeError("aporia_artifact_freeze_r5_metadata_path_invalid")
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
    if "mechanistic_r5" in relative:
        return "development:95001-95048,97001-97128;confirmation:96001-96048,98001-98128"
    if "operational_r4" in relative:
        return "development:99001-99256;confirmation:99301-99556"
    return "defined_in_preregistration_or_not_applicable"


def code_commitment(repository_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in FREEZE_RELATIVE_PATHS:
        path = repository_root / relative
        resolved = path.resolve()
        if path.is_symlink() or not path.is_file() or repository_root.resolve() not in resolved.parents:
            raise RuntimeError("aporia_artifact_freeze_r5_sealed_path_invalid")
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def assert_current_seal(repository_root: Path, expected_commitment: str | None = None) -> None:
    fixture_root = repository_root / "experiments" / "aporia-lacuna" / "fixtures"
    lock_path = fixture_root / "aporia_artifact_freeze_r5.lock.json"
    fixture_path = fixture_root / "aporia_artifact_freeze_r5.json"
    if lock_path.is_symlink() or fixture_path.is_symlink():
        raise RuntimeError("aporia_artifact_freeze_r5_seal_path_invalid")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    current = code_commitment(repository_root)
    if lock.get("status") != "sealed" or lock.get("code_commitment") != current:
        raise RuntimeError("aporia_artifact_freeze_r5_code_commitment_mismatch")
    if expected_commitment is not None and expected_commitment != current:
        raise RuntimeError("aporia_artifact_freeze_r5_external_commitment_mismatch")
    if lock.get("fixture_sha256") != hashlib.sha256(fixture_path.read_bytes()).hexdigest():
        raise RuntimeError("aporia_artifact_freeze_r5_fixture_commitment_mismatch")
    if int(lock.get("sealed_file_count", 0)) != len(FREEZE_RELATIVE_PATHS):
        raise RuntimeError("aporia_artifact_freeze_r5_file_count_mismatch")


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
