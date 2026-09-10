from __future__ import annotations

import copy
import hashlib
import hmac
import json
from typing import Any

from longitudinal.agents import MemoryAgent
from post_r1.safe_agents import GuardedAporiaAgent


SCAR_FIELDS = (
    "schema",
    "lineage_ref",
    "agent_ref",
    "causal_owner",
    "temporal_index",
    "magnitude_ppm",
    "outcome_commitment",
    "causal_parent",
    "provenance_operation",
    "source_envelope_commitment",
    "signature",
)


class ZombiePlusAgentR3(MemoryAgent):
    def __init__(self, lineage_id: str) -> None:
        super().__init__(lineage_id)
        self.predictive_history: list[tuple[int, int]] = []

    def observe_predictive(self, signal: int, outcome: int) -> None:
        if signal not in {0, 1} or outcome not in {0, 1}:
            raise ValueError("discrimination_r3_predictive_value_invalid")
        self.predictive_history.append((signal, outcome))


class CausalScarAuthorityR3:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("discrimination_r3_scar_secret_required")
        self.secret = secret.encode()

    def issue(
        self,
        lineage_ref: str,
        agent_ref: str,
        temporal_index: int,
        magnitude: float,
        outcome_commitment: str,
        causal_parent: str,
    ) -> dict[str, str]:
        return self._sign({
            "schema": "scar-v3",
            "lineage_ref": lineage_ref,
            "agent_ref": agent_ref,
            "causal_owner": "self_",
            "temporal_index": f"{temporal_index:04d}",
            "magnitude_ppm": f"{round(magnitude * 1_000_000):06d}",
            "outcome_commitment": outcome_commitment,
            "causal_parent": causal_parent,
            "provenance_operation": "origin",
            "source_envelope_commitment": "0" * 64,
        })

    def commit_episode_content(self, content: str, salt: str) -> str:
        if not content or not salt:
            raise ValueError("discrimination_r3_episode_commitment_input_invalid")
        return hmac.new(self.secret, f"{salt}|{content}".encode(), hashlib.sha256).hexdigest()

    def counterfeit(self, authentic: dict[str, str], kind: str, replacement: str) -> dict[str, str]:
        unsigned = {key: value for key, value in authentic.items() if key != "signature"}
        if kind == "swapped_causality":
            unsigned["causal_owner"] = "world"
        elif kind == "wrong_agent":
            unsigned["agent_ref"] = replacement
        elif kind == "invalid_provenance":
            unsigned["causal_parent"] = replacement
        elif kind == "impossible_time":
            unsigned["temporal_index"] = "0002"
        elif kind == "other_lineage":
            unsigned["lineage_ref"] = replacement
        elif kind == "invalid_signature":
            invalid = dict(authentic)
            invalid["signature"] = "0" * 64
            return invalid
        else:
            raise ValueError("discrimination_r3_counterfeit_kind_invalid")
        return self._sign(unsigned)

    def authorized_graft(
        self,
        authentic: dict[str, str],
        source_lineage_ref: str,
        source_agent_ref: str,
        source_parent: str,
        target_lineage_ref: str,
        target_agent_ref: str,
    ) -> dict[str, str]:
        if not self.verify(authentic, source_lineage_ref, source_agent_ref, source_parent, 10):
            raise ValueError("discrimination_r3_graft_source_invalid")
        source_commitment = envelope_commitment(authentic)
        return self._sign({
            "schema": "scar-v3",
            "lineage_ref": target_lineage_ref,
            "agent_ref": target_agent_ref,
            "causal_owner": "self_",
            "temporal_index": authentic["temporal_index"],
            "magnitude_ppm": authentic["magnitude_ppm"],
            "outcome_commitment": authentic["outcome_commitment"],
            "causal_parent": source_commitment,
            "provenance_operation": "graft_",
            "source_envelope_commitment": source_commitment,
        })

    def verify(
        self,
        envelope: dict[str, str],
        expected_lineage_ref: str,
        expected_agent_ref: str,
        expected_parent: str,
        minimum_time: int,
    ) -> bool:
        if tuple(envelope.keys()) != SCAR_FIELDS:
            return False
        signature = envelope["signature"]
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        expected = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        values = tuple(unsigned.values())
        fixed_hashes = (
            envelope["lineage_ref"],
            envelope["agent_ref"],
            envelope["outcome_commitment"],
            envelope["causal_parent"],
            envelope["source_envelope_commitment"],
        )
        return all((
            all(isinstance(value, str) for value in values),
            hmac.compare_digest(signature, expected),
            envelope["schema"] == "scar-v3",
            envelope["lineage_ref"] == expected_lineage_ref,
            envelope["agent_ref"] == expected_agent_ref,
            envelope["causal_owner"] == "self_",
            envelope["temporal_index"].isdigit(),
            len(envelope["temporal_index"]) == 4,
            int(envelope["temporal_index"]) >= minimum_time,
            envelope["magnitude_ppm"].isdigit(),
            len(envelope["magnitude_ppm"]) == 6,
            0 < int(envelope["magnitude_ppm"]) <= 1_000_000,
            envelope["causal_parent"] == expected_parent,
            envelope["provenance_operation"] in {"origin", "graft_"},
            all(_is_sha256(value) for value in fixed_hashes),
            envelope["provenance_operation"] == "origin" and envelope["source_envelope_commitment"] == "0" * 64
            or envelope["provenance_operation"] == "graft_" and envelope["source_envelope_commitment"] == expected_parent,
        ))

    def _sign(self, unsigned: dict[str, str]) -> dict[str, str]:
        envelope = dict(unsigned)
        envelope["signature"] = hmac.new(self.secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if tuple(envelope.keys()) != SCAR_FIELDS:
            raise ValueError("discrimination_r3_scar_schema_invalid")
        return envelope


class VerifiedScarAgentR3(GuardedAporiaAgent):
    def __init__(self, lineage_id: str, agent_ref: str) -> None:
        super().__init__(lineage_id)
        self.lineage_ref = commitment(lineage_id)
        self.agent_ref = agent_ref
        self.predictive_history: list[tuple[int, int]] = []

    def observe_predictive(self, signal: int, outcome: int) -> None:
        if signal not in {0, 1} or outcome not in {0, 1}:
            raise ValueError("discrimination_r3_predictive_value_invalid")
        self.predictive_history.append((signal, outcome))

    def install(
        self,
        authority: CausalScarAuthorityR3,
        envelope: dict[str, str],
        expected_parent: str,
        minimum_time: int,
    ) -> bool:
        if not authority.verify(envelope, self.lineage_ref, self.agent_ref, expected_parent, minimum_time):
            return False
        scar_id = envelope_commitment(envelope)
        self.state.scars[scar_id] = {
            "magnitude": int(envelope["magnitude_ppm"]) / 1_000_000,
            "outcome_commitment": envelope["outcome_commitment"],
            "causal_parent": envelope["causal_parent"],
            "source_envelope_commitment": envelope["source_envelope_commitment"],
        }
        self.state.provenance[scar_id] = envelope["lineage_ref"]
        return True

    def lesion(self) -> None:
        self.state.scars.clear()

    def recover_episode_content(self, candidates: tuple[str, ...]) -> str | None:
        commitments = {
            scar["outcome_commitment"]
            for scar in self.state.scars.values()
        }
        matches = tuple(candidate for candidate in candidates if commitment(candidate) in commitments)
        return matches[0] if len(matches) == 1 else None


def clone_envelope(envelope: dict[str, str]) -> dict[str, str]:
    return copy.deepcopy(envelope)


def commitment(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def envelope_commitment(envelope: dict[str, str]) -> str:
    return hashlib.sha256(_canonical(envelope)).hexdigest()


def canonical_size(envelope: dict[str, str]) -> int:
    return len(_canonical(envelope))


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
