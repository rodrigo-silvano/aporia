"""
Generic agent harness implementation for APORIA.
Allows any agent harness (LangChain, AutoGen, CrewAI, Antigravity, Custom)
to authenticate, ingest events, and query APORIA governance.
"""

from __future__ import annotations
from typing import Any, Mapping, Sequence
from aporia.crypto import jwt_decode, hash_equals
from aporia.harness.base import AgentHarness, HarnessAuthenticationError, HarnessContextError


class GenericHarness(AgentHarness):
    """
    Flexible harness adapter accepting configurable or standard JWT tokens,
    with stateless or database-backed session validation.
    """

    def __init__(
        self,
        allowed_issuers: Sequence[str] = ("*",),
        allowed_audiences: Sequence[str] = ("*",),
        allowed_source_services: Sequence[str] = ("*",),
        allow_any_source: bool = True,
    ) -> None:
        self.allowed_issuers = tuple(allowed_issuers)
        self.allowed_audiences = tuple(allowed_audiences)
        self.allowed_source_services = tuple(allowed_source_services)
        self.allow_any_source = allow_any_source

    @property
    def name(self) -> str:
        return "generic"

    def is_source_service_allowed(self, source_service: str) -> bool:
        if self.allow_any_source and bool(source_service.strip()):
            return True
        return source_service in self.allowed_source_services

    def verify_token(self, token: str, secret: str) -> dict[str, Any]:
        try:
            claims = jwt_decode(token, secret, algorithms=("HS256",), verify_exp=True)
        except Exception as exc:
            msg = str(exc).lower()
            if "expired" in msg:
                raise HarnessAuthenticationError("aporia_token_expired") from exc
            raise HarnessAuthenticationError("aporia_token_invalid") from exc

        iss = claims.get("iss", "")
        if self.allowed_issuers and iss not in self.allowed_issuers and "*" not in self.allowed_issuers:
            raise HarnessAuthenticationError("aporia_token_invalid")

        aud = claims.get("aud", "")
        if self.allowed_audiences and aud not in self.allowed_audiences and "*" not in self.allowed_audiences:
            raise HarnessAuthenticationError("aporia_token_invalid")

        tenant_id = claims.get("tenant_id")
        if not isinstance(tenant_id, int) or tenant_id < 1:
            raise HarnessContextError("aporia_tenant_context_required")

        return claims

    def verify_context(self, db_conn: Any, claims: Mapping[str, Any], input_data: Mapping[str, Any]) -> dict[str, Any]:
        tenant_id = claims.get("tenant_id")
        inp_tenant = input_data.get("tenant_id")
        if not isinstance(inp_tenant, int) or inp_tenant != tenant_id:
            raise HarnessContextError("aporia_tenant_forbidden")

        # Check recovery claims if present
        if claims.get("recovery") is True:
            if (
                not hash_equals(str(claims.get("recovery_event_id", "")), str(input_data.get("event_id", "")))
                or not hash_equals(str(claims.get("recovery_canonical_sha256", "")), str(input_data.get("canonical_sha256", "")))
                or int(claims.get("recovery_journal_sequence", 0)) != int(input_data.get("journal_sequence", 0))
            ):
                raise HarnessAuthenticationError("aporia_token_invalid")

        session_id = str(claims.get("session_id", "default-session"))
        turn_id = str(claims.get("turn_id", "")).strip()

        # Check if database has assistant_runtime_sessions and row exists
        context: dict[str, Any] = {
            "id": 1,
            "public_id": session_id,
            "owner_user_id": int(claims.get("owner_user_id", tenant_id)),
            "actor_user_id": int(claims.get("actor_user_id", tenant_id)),
            "tenant_id": tenant_id,
            "turn_id": turn_id,
            "role": str(claims.get("role", "owner")),
            "privacy_scope": str(claims.get("privacy_scope", "TENANT_PRIVATE")),
            "context_revision": int(claims.get("context_revision", 1)),
            "membership_id": int(claims.get("membership_id", 1)),
            "tenant_context_status": "resolved",
            "tenant_context_source": "canonical",
        }

        agent_session_id = str(claims.get("agent_session_id", claims.get("session_id", session_id)))
        try:
            stmt = db_conn.prepare(
                "SELECT id, public_id, owner_user_id, actor_user_id FROM assistant_runtime_sessions WHERE public_id = ? OR agent_session_id = ? LIMIT 1"
            )
            stmt.execute([session_id, agent_session_id])
            row = stmt.fetch()
            if row:
                context["id"] = row["id"]
        except Exception:
            try:
                stmt = db_conn.prepare(
                    "SELECT id, public_id, owner_user_id, actor_user_id FROM assistant_runtime_sessions WHERE public_id = ? LIMIT 1"
                )
                stmt.execute([session_id])
                row = stmt.fetch()
                if row:
                    context["id"] = row["id"]
            except Exception:
                pass

        return context
