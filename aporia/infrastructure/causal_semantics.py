"""
Aporia Causal Event Semantics V2.
Encodes producing/requiring masks and event codes for causal DAG tracing.
"""

from __future__ import annotations


class AporiaCausalEventSemanticsV2:
    SEMANTICS = {
        "root": (0, 0, 0.0),
        "turn.started": (1, 0, 0.1),
        "tool.proposed": (2, 1, 0.2),
        "approval.requested": (4, 2, 0.3),
        "effect.started": (8, 6, 0.4),
        "effect.completed": (1, 8, 0.5),
        "turn.completed": (1, 15, 0.6),
        "turn.failed": (1, 15, 0.7),
        "turn.interrupted": (1, 15, 0.8),
        "turn.deduplicated": (1, 1, 0.9),
    }

    @classmethod
    def produces_mask(cls, event_kind: str) -> int:
        return cls._semantics(event_kind)[0]

    @classmethod
    def producesMask(cls, event_kind: str) -> int:
        return cls.produces_mask(event_kind)

    @classmethod
    def requires_any_mask(cls, event_kind: str) -> int:
        return cls._semantics(event_kind)[1]

    @classmethod
    def requiresAnyMask(cls, event_kind: str) -> int:
        return cls.requires_any_mask(event_kind)

    @classmethod
    def code(cls, event_kind: str) -> float:
        return cls._semantics(event_kind)[2]

    @classmethod
    def event_kinds(cls) -> list[str]:
        return [k for k in cls.SEMANTICS if k != "root"]

    @classmethod
    def eventKinds(cls) -> list[str]:
        return cls.event_kinds()

    @classmethod
    def _semantics(cls, event_kind: str) -> tuple[int, int, float]:
        if event_kind not in cls.SEMANTICS:
            raise ValueError("aporia_causal_event_kind_unsupported")
        return cls.SEMANTICS[event_kind]
