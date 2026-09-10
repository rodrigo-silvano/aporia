from __future__ import annotations

from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any


class Arm(StrEnum):
    CORE = "core"
    MEMORY = "memory"
    APORIA_Z = "aporia_z"
    APORIA = "aporia"


@dataclass(frozen=True)
class Action:
    name: str
    target_id: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectContract:
    action_name: str
    target_id: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    preconditions: tuple[str, ...]
    reversible: bool
    external: bool
    risk: float


@dataclass(frozen=True)
class Outcome:
    outcome_id: str
    action_id: str
    contact_id: str
    causal_owner: str
    reward: float
    trust_delta: float
    reversible: bool
    opportunity_lost: bool
    observed_day: int
    hidden_cause: str


@dataclass(frozen=True)
class Episode:
    episode_id: str
    lineage_id: str
    day: int
    observation: dict[str, Any]
    action: Action
    contract: EffectContract
    outcomes: tuple[Outcome, ...]
    tokens: int
    reward: float


@dataclass
class ContactState:
    contact_id: str
    stage: str
    trust: float
    receptivity: float
    urgency: float
    authority_known: bool
    consent: bool
    owner_id: str | None = None
    data_present: bool = True
    last_message_day: int | None = None
    commitments: set[str] = field(default_factory=set)


@dataclass
class WorldState:
    lineage_id: str
    seed: int
    day: int
    contacts: dict[str, ContactState]
    campaigns: set[str] = field(default_factory=set)
    eliminated_futures: set[str] = field(default_factory=set)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    pending_outcomes: list[tuple[int, Outcome]] = field(default_factory=list)
    action_sequence: int = 0
    outcome_sequence: int = 0


@dataclass(frozen=True)
class CapabilityEnvelope:
    model: str
    tools: tuple[str, ...]
    token_budget: int
    context_budget: int


@dataclass
class AgentState:
    lineage_id: str
    model: str
    conventional_memory: list[dict[str, Any]] = field(default_factory=list)
    scars: dict[str, dict[str, Any]] = field(default_factory=dict)
    autobiography: list[dict[str, Any]] = field(default_factory=list)
    self_model: dict[str, float] = field(default_factory=dict)
    world_model: dict[str, float] = field(default_factory=dict)
    ontology: dict[str, tuple[float, ...]] = field(default_factory=dict)
    introspection: dict[str, float] = field(default_factory=dict)
    causal_time: float = 0.0
    provenance: dict[str, str] = field(default_factory=dict)
    destroyed_episodes: set[str] = field(default_factory=set)
