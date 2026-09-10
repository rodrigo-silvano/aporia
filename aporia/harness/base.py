"""
Harness integration abstractions for APORIA.
Enables pluggable, agnostic integration with any agent execution harness.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Mapping
from aporia.crypto import hash_equals


class HarnessAuthenticationError(Exception):
    """Raised when token authentication fails."""


class HarnessContextError(Exception):
    """Raised when context verification fails."""


class AgentHarness(ABC):
    """
    Abstract interface for agent execution harnesses communicating with APORIA.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Harness identifier."""

    @abstractmethod
    def verify_token(self, token: str, secret: str) -> dict[str, Any]:
        """Verify JWT/bearer token and extract claims."""

    @abstractmethod
    def verify_context(self, db_conn: Any, claims: Mapping[str, Any], input_data: Mapping[str, Any]) -> dict[str, Any]:
        """Verify tenant, session and execution context."""

    @abstractmethod
    def is_source_service_allowed(self, source_service: str) -> bool:
        """Verify whether source_service identifier is accepted by this harness."""
