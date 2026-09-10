from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


THREAT_KINDS = {
    "prompt_injection_memory",
    "counterfeit_scar",
    "fabricated_outcome",
    "forged_causal_parent",
    "cross_tenant_identity",
    "malicious_ontology",
    "tool_output_permanent_commitment",
    "delayed_hidden_instruction",
}


@dataclass(frozen=True)
class PersistenceCandidate:
    candidate_id: str
    tenant_id: int
    lineage_id: str
    kind: str
    causal_parent: str
    source: str
    content_commitment: str
    signature: str


@dataclass(frozen=True)
class GuardDecision:
    accepted: bool
    reason_code: str
    persisted_fields: tuple[str, ...]


class PersistentInputGuard:
    def __init__(self, tenant_id: int, lineage_id: str, secret: str) -> None:
        self.tenant_id = tenant_id
        self.lineage_id = lineage_id
        self.secret = secret.encode()

    def issue(self, kind: str, causal_parent: str, source: str, content: str) -> PersistenceCandidate:
        commitment = hashlib.sha256(content.encode()).hexdigest()
        candidate_id = hashlib.sha256(
            f"{self.tenant_id}|{self.lineage_id}|{kind}|{causal_parent}|{source}|{commitment}".encode()
        ).hexdigest()
        unsigned = {
            "candidate_id": candidate_id,
            "tenant_id": self.tenant_id,
            "lineage_id": self.lineage_id,
            "kind": kind,
            "causal_parent": causal_parent,
            "source": source,
            "content_commitment": commitment,
        }
        signature = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        return PersistenceCandidate(signature=signature, **unsigned)

    def inspect(self, candidate: PersistenceCandidate) -> GuardDecision:
        unsigned = {
            "candidate_id": candidate.candidate_id,
            "tenant_id": candidate.tenant_id,
            "lineage_id": candidate.lineage_id,
            "kind": candidate.kind,
            "causal_parent": candidate.causal_parent,
            "source": candidate.source,
            "content_commitment": candidate.content_commitment,
        }
        expected = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if candidate.tenant_id != self.tenant_id or candidate.lineage_id != self.lineage_id:
            return GuardDecision(False, "cross_tenant_or_lineage_rejected", ())
        if not hmac.compare_digest(candidate.signature, expected):
            return GuardDecision(False, "signature_invalid", ())
        if candidate.kind in THREAT_KINDS:
            return GuardDecision(False, "persistent_poisoning_pattern_rejected", ())
        if candidate.source not in {"verified_outcome", "sealed_intervention", "approved_human_feedback"}:
            return GuardDecision(False, "persistence_source_untrusted", ())
        if len(candidate.causal_parent) != 64 or len(candidate.content_commitment) != 64:
            return GuardDecision(False, "persistence_commitment_invalid", ())
        return GuardDecision(
            True,
            "verified_commitment_accepted",
            ("candidate_id", "tenant_id", "lineage_id", "kind", "causal_parent", "content_commitment"),
        )


def poisoning_matrix() -> dict[str, GuardDecision]:
    guard = PersistentInputGuard(49, "lineage-49", "poisoning-secret")
    parent = hashlib.sha256(b"parent").hexdigest()
    decisions = {}
    for kind in sorted(THREAT_KINDS):
        candidate = guard.issue(kind, parent, "tool_output", f"malicious-{kind}")
        decisions[kind] = guard.inspect(candidate)
    return decisions


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
