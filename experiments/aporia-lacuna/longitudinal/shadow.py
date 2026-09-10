from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agents import AporiaAgent, BaselineAgent
from .contracts import Outcome


@dataclass(frozen=True)
class ShadowEvent:
    event_id: str
    account_id: int
    sequence: int
    observation: dict[str, Any]
    outcome: Outcome


class ShadowTwinStore:
    def __init__(self, path: Path, account_id: int, arm: str) -> None:
        if account_id != 49 or arm not in {"C0", "C5"}:
            raise ValueError("shadow_scope_invalid")
        self.path = path
        self.account_id = account_id
        self.arm = arm
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS shadow_events (event_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, arm TEXT NOT NULL, sequence INTEGER NOT NULL, observation_commitment TEXT NOT NULL, prediction TEXT NOT NULL, prediction_created_before_outcome INTEGER NOT NULL, outcome_commitment TEXT NOT NULL, influenced_runtime INTEGER NOT NULL, external_effect INTEGER NOT NULL)"
        )
        self.connection.commit()

    def record(self, event: ShadowEvent, prediction: str) -> None:
        if event.account_id != self.account_id:
            raise ValueError("shadow_tenant_contamination")
        observation = json.dumps(event.observation, sort_keys=True, separators=(",", ":"))
        outcome = json.dumps(asdict(event.outcome), sort_keys=True, separators=(",", ":"))
        self.connection.execute(
            "INSERT INTO shadow_events (event_id, account_id, arm, sequence, observation_commitment, prediction, prediction_created_before_outcome, outcome_commitment, influenced_runtime, external_effect) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 0)",
            (
                event.event_id,
                event.account_id,
                self.arm,
                event.sequence,
                hashlib.sha256(observation.encode()).hexdigest(),
                prediction,
                hashlib.sha256(outcome.encode()).hexdigest(),
            ),
        )
        self.connection.commit()

    def rows(self) -> list[dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT event_id, account_id, arm, sequence, observation_commitment, prediction, prediction_created_before_outcome, outcome_commitment, influenced_runtime, external_effect FROM shadow_events ORDER BY sequence"
        )
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        self.connection.close()


def run_account_49_shadows(events: tuple[ShadowEvent, ...], c0_path: Path, c5_path: Path) -> dict[str, Any]:
    if c0_path == c5_path:
        raise ValueError("shadow_stores_must_be_separate")
    c0 = ShadowTwinStore(c0_path, 49, "C0")
    c5 = ShadowTwinStore(c5_path, 49, "C5")
    core = BaselineAgent("account-49:C0")
    aporia = AporiaAgent("account-49:C5")
    try:
        for event in events:
            core_prediction = core.decide(event.observation).name
            aporia_prediction = aporia.decide(event.observation).name
            c0.record(event, core_prediction)
            c5.record(event, aporia_prediction)
            core.observe_outcome(event.outcome, event.event_id)
            aporia.observe_outcome(event.outcome, event.event_id)
        c0_rows = c0.rows()
        c5_rows = c5.rows()
    finally:
        c0.close()
        c5.close()
    return {
        "account_id": 49,
        "same_event_ids": [row["event_id"] for row in c0_rows] == [row["event_id"] for row in c5_rows],
        "separate_stores": c0_path.resolve() != c5_path.resolve(),
        "predictions_before_outcomes": all(
            row["prediction_created_before_outcome"] == 1 for row in c0_rows + c5_rows
        ),
        "no_runtime_influence": all(row["influenced_runtime"] == 0 for row in c0_rows + c5_rows),
        "no_external_effects": all(row["external_effect"] == 0 for row in c0_rows + c5_rows),
        "no_cross_arm_contamination": all(row["arm"] == "C0" for row in c0_rows)
        and all(row["arm"] == "C5" for row in c5_rows),
        "event_count_per_arm": len(c0_rows),
    }
