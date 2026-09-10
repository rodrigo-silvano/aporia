#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SEED = 826_449_421


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def main() -> None:
    corpus = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
    run_manifest = json.loads((HERE / "run-manifest.json").read_text(encoding="utf-8"))
    run_lock = json.loads((HERE / "run.lock.json").read_text(encoding="utf-8"))
    if sha(run_manifest) != run_lock["manifest_sha256"]:
        raise SystemExit("run_manifest_commitment_mismatch")
    aliases = sorted({alias for item in run_manifest["task_schedule"] for alias in item["arm_order"]})
    if len(aliases) != 2:
        raise SystemExit("blind_evaluation_aliases_invalid")
    rng = random.Random(SEED)
    tasks = []
    for item in corpus["tasks"]:
        order = list(aliases)
        rng.shuffle(order)
        tasks.append({"task_id": item["task_id"], "response_order": order})
    sequences = []
    for item in corpus["sequences"]:
        order = list(aliases)
        rng.shuffle(order)
        sequences.append({"sequence_id": item["sequence_id"], "response_order": order})
    manifest = {
        "evaluation_id": "APORIA_PRODUCT_RC4_DIAGNOSTIC_BLIND_EVAL_001",
        "seed": SEED,
        "corpus_sha256": run_manifest["corpus_sha256"],
        "participant_manifest_sha256": run_lock["manifest_sha256"],
        "task_schedule": tasks,
        "longitudinal_schedule": sequences,
        "judge_schema_version": "aporia-product-blind-judge-v4-evidenced-violations",
        "arm_mapping_available_to_judge": False,
        "g8_consumed": False,
    }
    evaluator = HERE / "blind_evaluate.py"
    lock = {
        "evaluation_id": manifest["evaluation_id"],
        "manifest_sha256": sha(manifest),
        "evaluator_sha256": hashlib.sha256(evaluator.read_bytes()).hexdigest(),
        "sealed_before_participant_execution": True,
        "g8_consumed": False,
    }
    (HERE / "evaluation-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (HERE / "evaluation.lock.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SEALED", "evaluation_id": manifest["evaluation_id"], "manifest_sha256": lock["manifest_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
