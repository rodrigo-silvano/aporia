"""
Unit tests for APORIA agent harness adapters and decoupled architecture.
Verifies universal harness interoperability across any agent framework and model.
"""

from __future__ import annotations
import unittest
import time
import hashlib

from aporia.crypto import jwt_encode
from aporia.harness import (
    AgentHarness,
    GenericHarness,
    HarnessRegistry,
    DEFAULT_REGISTRY,
    HarnessAuthenticationError,
    HarnessContextError,
)
from aporia import AporiaClient
from aporia.infrastructure.db import Connection
from aporia.infrastructure.schema import init_schema


class TestHarnessAdapters(unittest.TestCase):
    def setUp(self) -> None:
        self.pdo = Connection(":memory:")
        init_schema(self.pdo)
        self.secret = "test-bridge-secret-for-harness"

    def test_registry_resolution(self) -> None:
        registry = HarnessRegistry()
        generic = registry.get("generic")
        self.assertIsInstance(generic, GenericHarness)
        self.assertEqual(generic.name, "generic")

        # Test resolve by token from any agent framework
        for issuer in ["antigravity", "langchain", "autogen", "crewai", "custom-agent-gateway"]:
            token = jwt_encode({"iss": issuer}, self.secret)
            resolved = registry.resolve(token)
            self.assertIsInstance(resolved, GenericHarness)

    def test_generic_harness_verification(self) -> None:
        harness = GenericHarness()
        claims = {
            "iss": "aporia-agent-gateway",
            "aud": "aporia-observer",
            "jti": "jwt-id-12345",
            "tenant_id": 49,
            "membership_id": 1049,
            "owner_user_id": 49,
            "actor_user_id": 49,
            "role": "owner",
            "privacy_scope": "TENANT_PRIVATE",
            "context_revision": 1,
            "tenant_context_status": "resolved",
            "tenant_context_source": "canonical",
            "tenant_context_required": True,
            "session_id": "session-pub-49",
            "agent_session_id": "agent-session-49",
            "turn_id": "turn-49",
            "recovery": True,
            "recovery_event_id": "evt-123",
            "recovery_canonical_sha256": "digest-123",
            "recovery_journal_sequence": 1,
        }
        token = jwt_encode(claims, self.secret)
        verified_claims = harness.verify_token(token, self.secret)
        self.assertEqual(verified_claims["tenant_id"], 49)
        self.assertTrue(harness.is_source_service_allowed("agent-gateway"))
        self.assertTrue(harness.is_source_service_allowed("custom-runtime"))

        # Setup assistant session in db
        self.pdo.prepare(
            """INSERT INTO assistant_runtime_sessions
             (public_id, owner_user_id, actor_user_id, agent_session_id, status)
             VALUES (?, ?, ?, ?, 'active')"""
        ).execute(["session-pub-49", 49, 49, "agent-session-49"])

        input_data = {
            "tenant_id": 49,
            "event_id": "evt-123",
            "canonical_sha256": "digest-123",
            "journal_sequence": 1,
        }
        context = harness.verify_context(self.pdo, verified_claims, input_data)
        self.assertEqual(context["tenant_id"], 49)
        self.assertEqual(context["turn_id"], "turn-49")

    def test_custom_harness_registration(self) -> None:
        class CustomHarness(AgentHarness):
            @property
            def name(self) -> str:
                return "custom-harness"

            def verify_token(self, token: str, secret: str) -> dict:
                return {"tenant_id": 100}

            def verify_context(self, db_conn, claims, input_data) -> dict:
                return {"tenant_id": 100}

            def is_source_service_allowed(self, source_service: str) -> bool:
                return True

        registry = HarnessRegistry()
        custom = CustomHarness()
        registry.register(custom)
        self.assertEqual(registry.get("custom-harness"), custom)

        token = jwt_encode({"iss": "custom-harness"}, self.secret)
        self.assertEqual(registry.resolve(token), custom)

    def test_aporia_client_integration(self) -> None:
        client = AporiaClient(pdo=self.pdo, secret=self.secret)
        self.assertEqual(client.harness.name, "generic")
        self.assertTrue(client.is_healthy())


if __name__ == "__main__":
    unittest.main()

