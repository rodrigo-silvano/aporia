"""
Ecological Session Context Backfill Prohibition for APORIA.
Enforces the strict prohibition against retroactive linking in ecological experiments.
"""

from __future__ import annotations
from typing import Any


class AporiaEcologicalSessionContextBackfill:
    """Prohibits retroactive linking of ecological session context."""

    def __init__(self, pdo: Any, environment: str) -> None:
        self.pdo = pdo
        self.environment = environment

    def run(self) -> dict[str, Any]:
        """Always raises runtime exception as retroactive linking is strictly prohibited."""
        raise RuntimeError("aporia_ecological_retroactive_linking_prohibited")
