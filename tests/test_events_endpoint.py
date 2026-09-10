"""Unit tests for Aporia Event API Endpoint."""
from __future__ import annotations

import os
import unittest
from aporia.api.events_endpoint import AporiaEventApiEndpoint
from aporia.crypto import jwt_encode
from aporia.infrastructure.db import Connection


class TestAporiaEventApiEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = Connection()
        self.conn.execute("""
            CREATE TABLE assistant_runtime_sessions (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                actor_user_id INTEGER NOT NULL,
                agent_session_id TEXT NOT NULL,
                profile_id INTEGER NULL,
                workspace_profile_id INTEGER NULL,
                relationship_id INTEGER NULL,
                status TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            INSERT INTO assistant_runtime_sessions
            (id, public_id, owner_user_id, actor_user_id, agent_session_id, profile_id, workspace_profile_id, relationship_id, status)
            VALUES
            (1, 'session-active', 49, 49, 'agent-active', 5, 7, 9, 'active'),
            (2, 'session-archived', 49, 49, 'agent-archived', 5, 7, 9, 'archived')
        """)
        os.environ.pop("HTTP_AUTHORIZATION", None)

    def tearDown(self) -> None:
        os.environ.pop("HTTP_AUTHORIZATION", None)

    def test_event_bound_recovery_accepts_active_and_archived_sessions(self) -> None:
        for session_id, agent_session_id in [
            ("session-active", "agent-active"),
            ("session-archived", "agent-archived"),
        ]:
            context = AporiaEventApiEndpoint.verifiedContext(
                self.conn,
                self._claims(session_id, agent_session_id),
                self._event(),
            )
            self.assertEqual(49, int(context["owner_user_id"]))
            self.assertEqual(7001, int(context["tenant_id"]))
            self.assertEqual("turn-public", context["turn_id"])

    def test_recovery_rejects_missing_canonical_tenant_context_before_session_lookup(self) -> None:
        claims = self._claims("session-active", "agent-active")
        for key in [
            "tenant_id", "membership_id", "role", "privacy_scope",
            "context_revision", "tenant_context_status", "tenant_context_source",
        ]:
            claims.pop(key, None)

        with self.assertRaises(RuntimeError) as ctx:
            AporiaEventApiEndpoint.verifiedContext(self.conn, claims, self._event())
        self.assertEqual("aporia_tenant_context_required", str(ctx.exception))

    def test_recovery_rejects_an_event_from_another_canonical_tenant(self) -> None:
        event = dict(self._event())
        event["tenant_id"] = 7002

        with self.assertRaises(RuntimeError) as ctx:
            AporiaEventApiEndpoint.verifiedContext(
                self.conn,
                self._claims("session-active", "agent-active"),
                event,
            )
        self.assertEqual("aporia_tenant_forbidden", str(ctx.exception))

    def test_recovery_token_cannot_authorize_another_event_hash_or_sequence(self) -> None:
        changes = [
            {"event_id": "different-event"},
            {"canonical_sha256": "c" * 64},
            {"journal_sequence": 8},
        ]
        for change in changes:
            modified_event = dict(self._event())
            modified_event.update(change)
            with self.assertRaises(RuntimeError) as ctx:
                AporiaEventApiEndpoint.verifiedContext(
                    self.conn,
                    self._claims("session-active", "agent-active"),
                    modified_event,
                )
            self.assertEqual("aporia_token_invalid", str(ctx.exception))

    def test_recovery_token_remains_tenant_and_session_scoped(self) -> None:
        claims = self._claims("session-active", "agent-active")
        claims["owner_user_id"] = 73

        with self.assertRaises(RuntimeError) as ctx:
            AporiaEventApiEndpoint.verifiedContext(self.conn, claims, self._event())
        self.assertEqual("runtime_session_invalid", str(ctx.exception))

    def test_signed_recovery_claims_require_canonical_tenant_context(self) -> None:
        claims = dict(self._claims("session-active", "agent-active"))
        claims.update({
            "iss": "aporia-agent-gateway",
            "aud": "aporia-observer",
            "jti": "jti-public",
        })
        secret = "b" * 32
        os.environ["HTTP_AUTHORIZATION"] = "Bearer " + jwt_encode(claims, secret)

        decoded = AporiaEventApiEndpoint.claims(secret)

        self.assertEqual(7001, decoded["tenant_id"])
        self.assertEqual("canonical", decoded["tenant_context_source"])

    def test_signed_recovery_claims_without_canonical_context_are_rejected(self) -> None:
        claims = dict(self._claims("session-active", "agent-active"))
        claims.update({
            "iss": "aporia-agent-gateway",
            "aud": "aporia-observer",
            "jti": "jti-public",
        })
        for key in [
            "tenant_id", "membership_id", "role", "privacy_scope",
            "context_revision", "tenant_context_status", "tenant_context_source",
        ]:
            claims.pop(key, None)

        secret = "b" * 32
        os.environ["HTTP_AUTHORIZATION"] = "Bearer " + jwt_encode(claims, secret)

        with self.assertRaises(RuntimeError) as ctx:
            AporiaEventApiEndpoint.claims(secret)
        self.assertEqual("aporia_tenant_context_required", str(ctx.exception))

    def _claims(self, session_id: str, agent_session_id: str) -> dict:
        return {
            "session_id": session_id,
            "agent_session_id": agent_session_id,
            "owner_user_id": 49,
            "actor_user_id": 49,
            "tenant_id": 7001,
            "membership_id": 9001,
            "role": "owner",
            "privacy_scope": "TENANT_PRIVATE",
            "context_revision": 4,
            "tenant_context_status": "resolved",
            "tenant_context_source": "canonical",
            "profile_id": 5,
            "workspace_profile_id": 7,
            "relationship_id": 9,
            "turn_id": "turn-public",
            "recovery": True,
            "recovery_event_id": "event-public",
            "recovery_canonical_sha256": "b" * 64,
            "recovery_journal_sequence": 7,
        }

    def _event(self) -> dict:
        return {
            "event_id": "event-public",
            "tenant_id": 7001,
            "canonical_sha256": "b" * 64,
            "journal_sequence": 7,
        }


if __name__ == "__main__":
    unittest.main()
