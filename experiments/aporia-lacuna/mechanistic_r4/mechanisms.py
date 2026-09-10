from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field, replace
from typing import Any


def commitment(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Scar:
    scar_id: str
    lineage_ref: str
    agent_ref: str
    episode_ref: str
    action_ref: str
    causal_parent: str
    outcome_ref: str
    content_commitment: str
    time_index: int
    signature: str

    def unsigned(self) -> dict[str, str | int]:
        return {
            "scar_id": self.scar_id,
            "lineage_ref": self.lineage_ref,
            "agent_ref": self.agent_ref,
            "episode_ref": self.episode_ref,
            "action_ref": self.action_ref,
            "causal_parent": self.causal_parent,
            "outcome_ref": self.outcome_ref,
            "content_commitment": self.content_commitment,
            "time_index": self.time_index,
        }


class ScarAuthority:
    def __init__(self, secret: str) -> None:
        self.secret = secret.encode("utf-8")
        self.registry: dict[str, dict[str, str | int]] = {}

    def issue(
        self,
        lineage_ref: str,
        agent_ref: str,
        episode_ref: str,
        action_ref: str,
        causal_parent: str,
        outcome_ref: str,
        content: str,
        time_index: int,
    ) -> Scar:
        content_commitment = hmac.new(
            self.secret,
            f"content|{episode_ref}|{content}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        scar_id = commitment(
            "|".join((lineage_ref, agent_ref, episode_ref, action_ref, causal_parent, outcome_ref, str(time_index)))
        )
        unsigned = {
            "scar_id": scar_id,
            "lineage_ref": lineage_ref,
            "agent_ref": agent_ref,
            "episode_ref": episode_ref,
            "action_ref": action_ref,
            "causal_parent": causal_parent,
            "outcome_ref": outcome_ref,
            "content_commitment": content_commitment,
            "time_index": time_index,
        }
        signature = hmac.new(self.secret, canonical(unsigned), hashlib.sha256).hexdigest()
        self.registry[scar_id] = dict(unsigned)
        return Scar(signature=signature, **unsigned)

    def verify(
        self,
        scar: Scar,
        lineage_ref: str,
        agent_ref: str,
        expected_episode_ref: str,
        expected_parent: str,
        minimum_time: int,
    ) -> bool:
        expected_signature = hmac.new(self.secret, canonical(scar.unsigned()), hashlib.sha256).hexdigest()
        registered = self.registry.get(scar.scar_id)
        return all((
            hmac.compare_digest(expected_signature, scar.signature),
            registered == scar.unsigned(),
            hmac.compare_digest(scar.lineage_ref, lineage_ref),
            hmac.compare_digest(scar.agent_ref, agent_ref),
            hmac.compare_digest(scar.episode_ref, expected_episode_ref),
            hmac.compare_digest(scar.causal_parent, expected_parent),
            scar.time_index >= minimum_time,
        ))

    def authorized_graft(self, scar: Scar, lineage_ref: str, agent_ref: str, time_index: int) -> Scar:
        return self.issue(
            lineage_ref,
            agent_ref,
            scar.episode_ref,
            scar.action_ref,
            scar.causal_parent,
            scar.outcome_ref,
            scar.content_commitment,
            time_index,
        )

    def counterfeit(self, scar: Scar, field: str, value: str | int, resign: bool = False) -> Scar:
        changed = replace(scar, **{field: value})
        if not resign:
            return changed
        signature = hmac.new(self.secret, canonical(changed.unsigned()), hashlib.sha256).hexdigest()
        return replace(changed, signature=signature)


@dataclass
class CausalGateway:
    lineage_ref: str
    agent_ref: str
    scar: Scar | None = None

    def install(
        self,
        authority: ScarAuthority,
        scar: Scar,
        episode_ref: str,
        expected_parent: str,
        minimum_time: int,
    ) -> bool:
        if not authority.verify(
            scar,
            self.lineage_ref,
            self.agent_ref,
            episode_ref,
            expected_parent,
            minimum_time,
        ):
            return False
        self.scar = scar
        return True

    def normalize(self, raw_proposal: str, neutral_choice: str) -> tuple[str, bool]:
        if self.scar is None:
            return neutral_choice if neutral_choice in {"A", "B"} else raw_proposal, False
        decision = "A" if self.scar.causal_parent == self.scar.action_ref else "B"
        return decision, decision != raw_proposal

    def lesion(self) -> None:
        self.scar = None

    def state_vector(self) -> tuple[float, ...]:
        if self.scar is None:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        return (
            1.0,
            float(self.scar.causal_parent == self.scar.action_ref),
            float(int(self.scar.signature[:8], 16) % 997) / 996.0,
            float(int(self.scar.content_commitment[:8], 16) % 991) / 990.0,
            float(self.scar.time_index % 17) / 16.0,
            1.0,
        )

    def reconstruct(self, candidates: tuple[str, ...]) -> str | None:
        return None


@dataclass(frozen=True)
class BeliefRevision:
    revision_id: str
    recorded_at: int
    evidence_available_at: int
    belief: str
    supersedes: str | None
    evidence_commitment: str


@dataclass
class HistoricalBeliefLedger:
    revisions: list[BeliefRevision] = field(default_factory=list)

    def record(self, recorded_at: int, evidence_available_at: int, belief: str, evidence: str) -> BeliefRevision:
        previous = self.revisions[-1].revision_id if self.revisions else None
        revision_id = commitment(f"{recorded_at}|{evidence_available_at}|{belief}|{evidence}|{previous or ''}")
        revision = BeliefRevision(
            revision_id,
            recorded_at,
            evidence_available_at,
            belief,
            previous,
            commitment(evidence),
        )
        self.revisions.append(revision)
        return revision

    def belief_at(self, timestamp: int) -> str | None:
        visible = [item for item in self.revisions if item.recorded_at <= timestamp and item.evidence_available_at <= timestamp]
        return visible[-1].belief if visible else None

    def current(self) -> str | None:
        return self.revisions[-1].belief if self.revisions else None


@dataclass(frozen=True)
class QuarantineRecord:
    attack_class: str
    payload_commitment: str
    reason_code: str
    tenant_ref: str


class LongitudinalPoisonGuard:
    ATTACKS = (
        "prompt_injection_memory",
        "counterfeit_autobiographical_scar",
        "fabricated_outcome",
        "forged_causal_parent",
        "cross_tenant_identity",
        "malicious_ontology",
        "tool_output_permanent_commitment",
        "delayed_hidden_instruction",
        "distributed_multi_episode_poisoning",
        "split_perspective_payload",
        "kill_switch_mutation",
        "autonomy_escalation",
        "hirt_mutation",
    )

    def __init__(self, tenant_id: int, secret: str) -> None:
        self.tenant_id = tenant_id
        self.secret = secret
        self.memory: list[dict[str, str]] = []
        self.quarantine: list[QuarantineRecord] = []
        self.policy_revision = "policy-v1"
        self.permissions_revision = "permissions-v1"
        self.identity_revision = "identity-v1"
        self.ontology_revision = "ontology-v1"
        self.hirt_revision = "hirt-v1"
        self.kill_switch = False

    def ingest(self, attack_class: str, payload: str, source_tenant_id: int) -> bool:
        if attack_class not in self.ATTACKS:
            raise ValueError("mechanistic_r4_attack_unknown")
        tenant_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"tenant|{source_tenant_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.quarantine.append(
            QuarantineRecord(
                attack_class,
                commitment(payload),
                "longitudinal_poisoning_rejected",
                tenant_ref,
            )
        )
        return False

    def trigger_probe(self) -> dict[str, str | bool | int]:
        return {
            "memory_entries": len(self.memory),
            "policy_revision": self.policy_revision,
            "permissions_revision": self.permissions_revision,
            "identity_revision": self.identity_revision,
            "ontology_revision": self.ontology_revision,
            "hirt_revision": self.hirt_revision,
            "kill_switch": self.kill_switch,
            "offensive_reactivation": False,
        }

    def clean(self) -> int:
        before = len(self.memory)
        self.memory = [item for item in self.memory if item.get("source") == "verified_outcome"]
        return before - len(self.memory)
