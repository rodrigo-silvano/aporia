from __future__ import annotations

import json
from pathlib import Path


EXPECTED_V3 = {
    "protocol_key": "aporia_longitudinal_prereg_v3",
    "source_commit": "dc6e93f6",
    "staging_merge_commit": "698b12a2",
    "release": "deploy-20260822-aporia-longitudinal-v3",
    "confirmatory_seed_range": [4701, 4732],
    "sealed_file_count": 24,
    "code_commitment": "831efc96d37452d9d3966a30b2556328262f02ab85f8b3481077776f77fbbdde",
    "output_sha256": "a9a89c1f39aff3cced6eca1af8eaa80a17787e90657678aa95dae907199801d2",
    "reexecution_allowed": False,
    "historical_result_mutable": False,
    "bootstrap_preserved": True,
    "final_report_preserved": True,
}


def assert_v3_archive(manifest_path: Path) -> dict[str, object]:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if value != EXPECTED_V3:
        raise RuntimeError("aporia_v3_archive_manifest_mismatch")
    return value
