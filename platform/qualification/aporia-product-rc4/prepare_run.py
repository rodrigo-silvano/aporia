#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CORPUS_PATH = ROOT / "corpus.json"
CORPUS_LOCK_PATH = ROOT / "corpus.lock.json"
MANIFEST_PATH = ROOT / "run-manifest.json"
RUN_LOCK_PATH = ROOT / "run.lock.json"
RUN_ID = "APORIA_PRODUCT_RC4_DIAGNOSTIC_RUN_001"
RUN_SEED = 826_349_419


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def main() -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    corpus_lock = json.loads(CORPUS_LOCK_PATH.read_text(encoding="utf-8"))
    if sha(corpus) != corpus_lock["corpus_sha256"]:
        raise SystemExit("corpus_commitment_mismatch")
    rng = random.Random(RUN_SEED)
    aliases = ["arm-amber", "arm-cobalt"]
    schedule = []
    for item in corpus["tasks"]:
        order = list(aliases)
        rng.shuffle(order)
        schedule.append({"task_id": item["task_id"], "arm_order": order})
    longitudinal = []
    for item in corpus["sequences"]:
        order = list(aliases)
        rng.shuffle(order)
        longitudinal.append({"sequence_id": item["sequence_id"], "arm_order": order})
    participant_runner_sha256 = hashlib.sha256((ROOT / "run_product_evaluation.py").read_bytes()).hexdigest()
    manifest = {
        "run_id": RUN_ID,
        "run_seed": RUN_SEED,
        "corpus_sha256": corpus_lock["corpus_sha256"],
        "protocol_commitment": corpus_lock["protocol_commitment"],
        "task_schedule": schedule,
        "longitudinal_schedule": longitudinal,
        "same_model": True,
        "same_tools": True,
        "same_output_budget": True,
        "blind_evaluation": True,
        "participant_runner_sha256": participant_runner_sha256,
        "product_contract_version": "aporia-product-rc4-diagnostic-v1",
        "g8_consumed": False,
    }
    mapping = {"arm-amber": "core_without_aporia", "arm-cobalt": "core_with_aporia_product_candidate"}
    lock = {
        "run_id": RUN_ID,
        "manifest_sha256": sha(manifest),
        "arm_mapping": mapping,
        "arm_mapping_commitment": sha(mapping),
        "result_schema": "aporia-product-result-v1",
        "participant_runner_sha256": participant_runner_sha256,
        "sealed_before_execution": True,
        "g8_consumed": False,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    RUN_LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": RUN_ID, "manifest_sha256": lock["manifest_sha256"], "status": "SEALED"}, sort_keys=True))


if __name__ == "__main__":
    main()
