"""
Unified APORIA Client.
High-level interface for integrating APORIA governance with any agent harness.
"""

from __future__ import annotations
import os
from typing import Any, Mapping

from aporia.infrastructure.db import get_connection, Connection
from aporia.harness.base import AgentHarness
from aporia.harness.registry import DEFAULT_REGISTRY, HarnessRegistry
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.config import Config


class AporiaClient:
    """Main client interface for interacting with APORIA services and pipelines."""

    def __init__(
        self,
        pdo: Any = None,
        secret: str = "",
        harness: str | AgentHarness = "generic",
        environment: str = "staging",
    ) -> None:
        self.pdo = pdo if pdo is not None else get_connection()
        self.secret = secret or os.getenv("APORIA_BRIDGE_SECRET", "")
        self.environment = environment
        
        if isinstance(harness, str):
            resolved = DEFAULT_REGISTRY.get(harness)
            if resolved is None:
                resolved = DEFAULT_REGISTRY.get("generic")
            self.harness: AgentHarness = resolved # type: ignore
        else:
            self.harness = harness

    def is_healthy(self) -> bool:
        """Checks if the database connection and core schema are operational."""
        try:
            stmt = self.pdo.prepare("SELECT 1")
            stmt.execute()
            return stmt.fetchColumn(0) == 1
        except Exception:
            return False

    @property
    def controls(self) -> AporiaIndependentControlPlane:
        return AporiaIndependentControlPlane(self.pdo, Config())
