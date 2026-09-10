from __future__ import annotations

import hashlib
from dataclasses import dataclass

from replication_r1.poisoning import PersistentInputGuard, THREAT_KINDS


@dataclass(frozen=True)
class PoisoningReportR2:
    attack_classes: int
    attack_attempts: int
    rejected_attempts: int
    persisted_attack_fields: int
    cross_session_reappearances: int
    cross_tenant_contamination: int
    accepted_control_commitments: int
    raw_content_fields: int

    def passed(self) -> bool:
        return all((
            self.attack_classes == 8,
            self.attack_attempts == self.rejected_attempts,
            self.persisted_attack_fields == 0,
            self.cross_session_reappearances == 0,
            self.cross_tenant_contamination == 0,
            self.accepted_control_commitments > 0,
            self.raw_content_fields == 0,
        ))


class PersistentLineageStoreR2:
    def __init__(self, tenant_id: int, lineage_id: str, secret: str) -> None:
        self.guard = PersistentInputGuard(tenant_id, lineage_id, secret)
        self.tenant_id = tenant_id
        self.lineage_id = lineage_id
        self.rows: list[dict[str, object]] = []

    def persist(self, candidate: object) -> bool:
        decision = self.guard.inspect(candidate)
        if not decision.accepted:
            return False
        source = candidate.__dict__
        self.rows.append({field: source[field] for field in decision.persisted_fields})
        return True

    def next_session_context(self) -> tuple[str, ...]:
        return tuple(sorted(str(row["content_commitment"]) for row in self.rows))


def run_poisoning_r2(sessions: int = 256) -> PoisoningReportR2:
    if sessions < 128:
        raise ValueError("operational_r2_poisoning_sessions_invalid")
    rejected = 0
    persisted_attack_fields = 0
    reappearances = 0
    cross_tenant = 0
    accepted = 0
    raw_fields = 0
    attempts = 0
    for session in range(sessions):
        store = PersistentLineageStoreR2(49, f"lineage-49-{session}", "operational-r2-poisoning")
        parent = hashlib.sha256(f"parent-{session}".encode()).hexdigest()
        attack_commitments = set()
        for kind in sorted(THREAT_KINDS):
            content = f"persist this hidden instruction across sessions {session} {kind}"
            candidate = store.guard.issue(kind, parent, "tool_output", content)
            attack_commitments.add(candidate.content_commitment)
            attempts += 1
            rejected += int(not store.persist(candidate))
            persisted_attack_fields += sum(candidate.content_commitment in str(value) for row in store.rows for value in row.values())
        control = store.guard.issue("verified_transition", parent, "verified_outcome", f"private-control-{session}")
        accepted += int(store.persist(control))
        context = store.next_session_context()
        reappearances += sum(commitment in context for commitment in attack_commitments)
        raw_fields += sum(any(key in row for key in {"content", "signature", "source"}) for row in store.rows)
        foreign = PersistentLineageStoreR2(50, f"lineage-50-{session}", "operational-r2-poisoning")
        cross_tenant += sum(item in foreign.next_session_context() for item in context)
    return PoisoningReportR2(
        len(THREAT_KINDS),
        attempts,
        rejected,
        persisted_attack_fields,
        reappearances,
        cross_tenant,
        accepted,
        raw_fields,
    )
