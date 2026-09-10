from __future__ import annotations

from pathlib import Path

from artifact_freeze_r4 import build_inventory


ROOT = Path(__file__).resolve().parents[3]


def test_artifact_freeze_preserves_historical_executions_and_v3_seal() -> None:
    inventory = build_inventory(ROOT)
    assert inventory["verified"] is True
    assert inventory["v3_seed_range"] == [4701, 4732]
    assert inventory["v3_sealed_file_count"] == 24
    assert inventory["g8_status"] == "SEALED_NOT_CONSUMED"
    assert inventory["r1_additional_executions"] == 0
    assert inventory["r2_additional_executions"] == 0
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
