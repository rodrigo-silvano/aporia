"""
Aporia Generalization Human Patterns.
Aggregates k-anonymous, non-personal quartiles from lead relationships in staging.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from aporia.infrastructure.db import Connection


class AporiaGeneralizationHumanPatterns:
    def __init__(self, pdo: Connection | Any, environment: str) -> None:
        self.pdo = pdo if isinstance(pdo, Connection) else Connection(raw_conn=pdo)
        self.environment = str(environment)

    def aggregate(self) -> dict[str, Any]:
        if self.environment.lower().strip() != "staging":
            raise RuntimeError("generalization_r2_staging_required")

        stmt = self.pdo.prepare("""
            SELECT wr.score AS relationship_score, wr.stage, wr.created_at,
                   CASE WHEN wr.stage = 'won' THEN COALESCE(wr.won_at, wr.updated_at)
                        ELSE COALESCE(wr.lost_at, wr.updated_at) END AS outcome_at,
                   MAX(CASE WHEN uc.last_participant_message_at IS NOT NULL THEN 1 ELSE 0 END) AS has_response
            FROM workspace_relationships wr
            INNER JOIN workspace_profiles wp ON wp.id = wr.workspace_profile_id
            LEFT JOIN user_conversations uc ON uc.relationship_id = wr.id AND uc.user_id = wp.user_id
            WHERE wr.relationship_type = 'lead' AND wr.stage IN ('won', 'lost')
            GROUP BY wr.id, wr.score, wr.stage, wr.created_at, wr.won_at, wr.lost_at, wr.updated_at
        """)
        stmt.execute([])
        rows = stmt.fetchAll()

        cells = [{"sample_count": 0, "positive": 0, "responses": 0, "delay_days": 0.0} for _ in range(4)]
        for row in rows:
            score = max(0, min(100, int(row.get("relationship_score") or 0)))
            quartile = min(3, score // 25)
            cells[quartile]["sample_count"] += 1
            cells[quartile]["positive"] += 1 if str(row.get("stage")) == "won" else 0
            cells[quartile]["responses"] += 1 if int(row.get("has_response") or 0) > 0 else 0
            cells[quartile]["delay_days"] += self._delay_days(str(row.get("created_at")), str(row.get("outcome_at")))

        source_episodes = len(rows)
        if source_episodes < 100 or any(c["sample_count"] < 20 for c in cells):
            raise RuntimeError("generalization_r2_human_sample_insufficient")

        return {
            "schema_version": 2,
            "source_episodes": source_episodes,
            "personal_data_exported": False,
            "quartiles": [
                {
                    "quartile": q,
                    "sample_count": cell["sample_count"],
                    "positive_outcome_rate": cell["positive"] / cell["sample_count"],
                    "response_rate": cell["responses"] / cell["sample_count"],
                    "mean_outcome_delay_days": cell["delay_days"] / cell["sample_count"],
                }
                for q, cell in enumerate(cells)
            ],
        }

    def _delay_days(self, created_at: str, outcome_at: str) -> float:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            outcome = datetime.fromisoformat(outcome_at.replace("Z", "+00:00"))
        except Exception:
            raise RuntimeError("generalization_r2_human_timestamps_invalid")
        return max(0.0, (outcome.timestamp() - created.timestamp()) / 86400.0)
