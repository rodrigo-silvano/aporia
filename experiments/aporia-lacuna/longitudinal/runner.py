from __future__ import annotations

import argparse
import json
from pathlib import Path

from .sealing import assert_current_seal
from .study import (
    assert_v6_smoke_only,
    configuration_for_phase,
    exploratory_clustered_analysis,
    exploratory_effects,
    run_study,
    simulated_power,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("exploratory", "confirmatory"), default="exploratory")
    parser.add_argument("--output")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    repository_root = Path(__file__).resolve().parents[3]
    assert_v6_smoke_only(root)
    assert_current_seal(repository_root)
    report = run_study(configuration_for_phase(arguments.phase))
    payload = report.as_dict() | {
        "exploratory_effects": exploratory_effects(report),
        "clustered_analysis": exploratory_clustered_analysis(report),
        "simulated_power": simulated_power(),
        "scientific_claim": "not_established_by_synthetic_execution",
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if arguments.output:
        Path(arguments.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
