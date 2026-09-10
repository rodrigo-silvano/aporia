"""
Aporia request integrity, canonical model request extraction and wire hashing.
"""

from __future__ import annotations
from typing import Any, Mapping, Sequence
from aporia.crypto import (
    canonical_json,
    prompt_wire_bytes,
    prompt_wire_hash,
    sha256_hex,
    opaque_identifier,
)


class AporiaRequestIntegrity:
    REQUEST_FIELDS = (
        "provider",
        "model",
        "model_snapshot",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "max_output_tokens",
        "max_tool_calls",
        "text",
        "service_tier",
        "store",
        "truncation",
        "previous_response_id",
        "conversation",
        "prepared_response_id",
        "websocket_lineage",
        "prompt_cache_key",
        "prompt_cache_options",
        "metadata",
    )

    DECISION_FIELDS = (
        "request_sha256",
        "aporia_state_revision",
        "aporia_state_sha256",
        "zombie_plus_state_sha256",
        "policy_version",
        "ontology_version",
        "hirt_version",
        "effect_registry_version",
        "gateway_version",
        "tenant_policy",
        "risk_threshold",
        "prepared_turn_lineage",
        "session_lineage",
        "turn_lineage",
        "outcome_visibility_state",
        "feature_flags",
        "kill_switch_state",
    )

    @staticmethod
    def canonical_json(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def canonicalJson(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def prompt_wire_bytes(instructions: str | None, messages: Sequence[Any]) -> bytes:
        return prompt_wire_bytes(instructions, messages)

    @staticmethod
    def promptWireBytes(instructions: str | None, messages: Sequence[Any]) -> bytes:
        return prompt_wire_bytes(instructions, messages)

    @staticmethod
    def prompt_wire_hash(instructions: str | None, messages: Sequence[Any]) -> str:
        return prompt_wire_hash(instructions, messages)

    @staticmethod
    def promptWireHash(instructions: str | None, messages: Sequence[Any]) -> str:
        return prompt_wire_hash(instructions, messages)

    @classmethod
    def canonical_model_request(cls, request: Mapping[str, Any], provider: str = "openai") -> dict[str, Any]:
        req = dict(request)
        req["provider"] = str(req.get("provider", "")).strip() or provider
        result: dict[str, Any] = {}
        for field in cls.REQUEST_FIELDS:
            result[field] = req.get(field)
        return result

    @classmethod
    def canonicalModelRequest(cls, request: Mapping[str, Any], provider: str = "openai") -> dict[str, Any]:
        return cls.canonical_model_request(request, provider)

    @classmethod
    def model_request_hash(cls, request: Mapping[str, Any], provider: str = "openai") -> str:
        return sha256_hex(canonical_json(cls.canonical_model_request(request, provider)))

    @classmethod
    def modelRequestHash(cls, request: Mapping[str, Any], provider: str = "openai") -> str:
        return cls.model_request_hash(request, provider)

    @classmethod
    def decision_context_hash(cls, context: Mapping[str, Any]) -> str:
        canonical = {field: context.get(field) for field in cls.DECISION_FIELDS}
        return sha256_hex(canonical_json(canonical))

    @classmethod
    def decisionContextHash(cls, context: Mapping[str, Any]) -> str:
        return cls.decision_context_hash(context)

    @staticmethod
    def opaque_identifier(secret: str, value: str) -> str:
        return opaque_identifier(secret, value)

    @staticmethod
    def opaqueIdentifier(secret: str, value: str) -> str:
        return opaque_identifier(secret, value)
