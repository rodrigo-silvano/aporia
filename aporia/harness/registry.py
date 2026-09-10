"""
Registry for resolving agent harnesses in APORIA.
"""

from __future__ import annotations
import json
from typing import Mapping
from aporia.crypto import _b64_url_decode
from aporia.harness.base import AgentHarness
from aporia.harness.generic import GenericHarness


class HarnessRegistry:
    """
    Manages and resolves harnesses based on token metadata or explicit config.
    """

    def __init__(self) -> None:
        self._harnesses: dict[str, AgentHarness] = {}
        self.register(GenericHarness())

    def register(self, harness: AgentHarness) -> None:
        self._harnesses[harness.name] = harness

    def get(self, name: str) -> AgentHarness | None:
        return self._harnesses.get(name)

    def resolve(self, token: str, default_harness: str = "generic") -> AgentHarness:
        """
        Inspect token payload (unverified issuer check) to determine
        the appropriate harness adapter if a specific one is registered.
        Otherwise, returns the universal GenericHarness.
        """
        try:
            parts = token.strip().split(".")
            if len(parts) == 3:
                payload = json.loads(_b64_url_decode(parts[1]).decode("utf-8"))
                iss = str(payload.get("iss", ""))
                if iss in self._harnesses:
                    return self._harnesses[iss]
                for name, h in self._harnesses.items():
                    if name in iss:
                        return h
        except Exception:
            pass

        return self._harnesses.get(default_harness) or self._harnesses["generic"]


DEFAULT_REGISTRY = HarnessRegistry()


def get_harness(name: str = "generic") -> AgentHarness:
    return DEFAULT_REGISTRY.get(name) or DEFAULT_REGISTRY._harnesses["generic"]


def resolve_harness(token: str, default_harness: str = "generic") -> AgentHarness:
    return DEFAULT_REGISTRY.resolve(token, default_harness)

