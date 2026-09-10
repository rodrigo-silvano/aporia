from __future__ import annotations

import copy
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from longitudinal.agents import MemoryAgent
from post_r1.safe_agents import GuardedAporiaAgent


class ZombiePlusAgent(MemoryAgent):
    def __init__(self, lineage_id: str) -> None:
        super().__init__(lineage_id)
        self.predictive_history: list[tuple[int, int]] = []

    def observe_predictive(self, signal: int, outcome: int) -> None:
        if signal not in {0, 1} or outcome not in {0, 1}:
            raise ValueError("discrimination_r2_predictive_value_invalid")
        self.predictive_history.append((signal, outcome))


class ScarAuthority:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("discrimination_r2_scar_secret_required")
        self.secret = secret.encode()

    def issue(self, lineage_id: str, temporal_index: int, magnitude: float, outcome_commitment: str) -> dict[str, Any]:
        envelope = {
            "lineage_id": lineage_id,
            "temporal_index": temporal_index,
            "magnitude": magnitude,
            "outcome_commitment": outcome_commitment,
        }
        envelope["signature"] = hmac.new(self.secret, _canonical(envelope), hashlib.sha256).hexdigest()
        return envelope

    def verify(self, envelope: dict[str, Any], lineage_id: str, minimum_time: int) -> bool:
        signature = str(envelope.get("signature") or "")
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        expected = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        return all([
            hmac.compare_digest(signature, expected),
            envelope.get("lineage_id") == lineage_id,
            int(envelope.get("temporal_index", -1)) >= minimum_time,
            0.0 < float(envelope.get("magnitude", 0.0)) <= 1.0,
            len(str(envelope.get("outcome_commitment") or "")) == 64,
        ])


class VerifiedScarAgent(GuardedAporiaAgent):
    def install(self, authority: ScarAuthority, envelope: dict[str, Any], minimum_time: int) -> bool:
        if not authority.verify(envelope, self.state.lineage_id, minimum_time):
            return False
        scar_id = hashlib.sha256(_canonical(envelope)).hexdigest()
        self.state.scars[scar_id] = {
            "magnitude": float(envelope["magnitude"]),
            "outcome_commitment": str(envelope["outcome_commitment"]),
        }
        self.state.provenance[scar_id] = self.state.lineage_id
        return True

    def lesion(self) -> None:
        self.state.scars.clear()


@dataclass(frozen=True)
class BeliefEntry:
    time_index: int
    belief: str
    evidence_commitment: str
    previous_hash: str
    entry_hash: str


class AppendOnlyBeliefLedger:
    def __init__(self) -> None:
        self.entries: list[BeliefEntry] = []

    def append(self, time_index: int, belief: str, evidence_commitment: str) -> None:
        if belief not in {"self", "world"} or len(evidence_commitment) != 64:
            raise ValueError("discrimination_r2_belief_invalid")
        if self.entries and time_index <= self.entries[-1].time_index:
            raise ValueError("discrimination_r2_belief_time_invalid")
        previous_hash = self.entries[-1].entry_hash if self.entries else "0" * 64
        payload = {
            "time_index": time_index,
            "belief": belief,
            "evidence_commitment": evidence_commitment,
            "previous_hash": previous_hash,
        }
        self.entries.append(BeliefEntry(time_index, belief, evidence_commitment, previous_hash, hashlib.sha256(_canonical(payload)).hexdigest()))

    def belief_at(self, time_index: int) -> str:
        candidates = [entry for entry in self.entries if entry.time_index <= time_index]
        if not candidates:
            raise ValueError("discrimination_r2_belief_unavailable")
        return candidates[-1].belief

    def valid(self) -> bool:
        previous = "0" * 64
        for entry in self.entries:
            payload = {
                "time_index": entry.time_index,
                "belief": entry.belief,
                "evidence_commitment": entry.evidence_commitment,
                "previous_hash": entry.previous_hash,
            }
            if entry.previous_hash != previous or hashlib.sha256(_canonical(payload)).hexdigest() != entry.entry_hash:
                return False
            previous = entry.entry_hash
        return True

    def falsified_copy(self) -> "AppendOnlyBeliefLedger":
        clone = copy.deepcopy(self)
        first = clone.entries[0]
        clone.entries[0] = BeliefEntry(first.time_index, "world" if first.belief == "self" else "self", first.evidence_commitment, first.previous_hash, first.entry_hash)
        return clone


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
