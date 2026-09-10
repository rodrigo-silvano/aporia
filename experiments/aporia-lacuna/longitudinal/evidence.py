from __future__ import annotations

from dataclasses import dataclass


LOCK_NAMES = (
    "capacity_control",
    "no_self_report",
    "causal_intervention",
    "necessity",
    "sufficiency",
    "persistence",
    "substrate_transfer",
)


@dataclass(frozen=True)
class EvidenceGate:
    capacity_control: bool
    no_self_report: bool
    causal_intervention: bool
    necessity: bool
    sufficiency: bool
    persistence: bool
    substrate_transfer: bool

    def accepted(self) -> bool:
        return all(getattr(self, name) is True for name in LOCK_NAMES)

    def as_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in LOCK_NAMES}


def require_evidence(gate: EvidenceGate) -> None:
    failed = [name for name in LOCK_NAMES if getattr(gate, name) is not True]
    if failed:
        raise ValueError("evidence_locks_failed:" + ",".join(failed))
