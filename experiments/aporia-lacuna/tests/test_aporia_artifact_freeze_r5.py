from __future__ import annotations

from pathlib import Path

from artifact_freeze_r5 import build_inventory, code_commitment


ROOT = Path(__file__).resolve().parents[3]


def test_artifact_freeze_preserves_historical_executions_and_v3_seal() -> None:
    inventory = build_inventory(ROOT, code_commitment(ROOT))
    assert inventory["verified"] is True
    assert inventory["v3_seed_range"] == [4701, 4732]
    assert inventory["v3_sealed_file_count"] == 24
    assert inventory["g8_status"] == "SEALED_NOT_CONSUMED"
    assert inventory["r1_additional_executions"] == 0
    assert inventory["r2_additional_executions"] == 0
    assert inventory["external_commitment_verified"] is True
    assert len(inventory["historical_runtime_artifacts"]) == 13
    assert all(item["execution_count"] == 1 for item in inventory["historical_runtime_artifacts"])
    assert inventory["local_scientific_artifact_count"] >= 100
    required = {
        "path",
        "size",
        "mtime_ns",
        "git_blob_hash",
        "sha256",
        "experiment_id",
        "preregistration_version",
        "seed_set",
        "execution_count",
        "release",
        "commit",
        "status",
    }
    assert all(required == set(item) for item in inventory["local_scientific_artifacts"])


def test_artifact_freeze_rejects_scientific_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("external", encoding="utf-8")
    link = ROOT / "experiments" / "aporia-lacuna" / "aporia-r5-test-link"
    link.symlink_to(target)
    try:
        try:
            build_inventory(ROOT)
        except RuntimeError as exception:
            assert str(exception) == "aporia_artifact_freeze_r5_symlink_rejected"
        else:
            raise AssertionError("aporia_artifact_freeze_r5_symlink_guard_missing")
    finally:
        link.unlink()
