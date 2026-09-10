#!/usr/bin/env python3
"""
APORIA Generalization Human Patterns CLI.
Aggregates R2 human patterns and emits cryptographic commitment digest.
"""

from __future__ import annotations
import sys
import os
import json
import hashlib

from aporia.infrastructure.db import get_connection
from aporia.infrastructure.experiments import AporiaGeneralizationHumanPatterns


def main() -> int:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    storage = os.path.join(base_dir, "runtime", "storage", "aporia-r1")
    if not os.path.isdir(storage) or os.path.islink(storage):
        # Create storage directory if absent in test/staging environment
        os.makedirs(storage, exist_ok=True)

    output = os.path.join(storage, "generalization-r2-human-patterns.json")
    if os.path.exists(output) or os.path.islink(output):
        sys.stderr.write(json.dumps({"ok": False, "error": "output_already_exists"}) + "\n")
        return 2

    pdo = get_connection()
    env = os.getenv("APP_ENV", "unknown")

    try:
        patterns = AporiaGeneralizationHumanPatterns(pdo, env)
        result = patterns.aggregate()
        rendered = json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n"

        with open(output, "w", encoding="utf-8") as f:
            f.write(rendered)
        os.chmod(output, 0o600)

        commitment = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        print(json.dumps({
            "ok": True,
            "source_episodes": result["source_episodes"],
            "output_sha256": commitment,
            "personal_data_exported": False,
        }, separators=(",", ":")))
        return 0
    except Exception as exc:
        err_msg = str(exc) if isinstance(exc, RuntimeError) else "generalization_r2_human_patterns_failed"
        sys.stderr.write(json.dumps({"ok": False, "error": err_msg}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
