"""
Internal Event API Endpoint for APORIA.
Accepts, authenticates, verifies, ingests, and projects agent runtime events.
Integrates through universal agent harness adapters and independent control plane.
"""

from __future__ import annotations
import os
import json
import secrets
import hmac
from typing import Any

from aporia.crypto import jwt_decode
from aporia.application.runtime_mode_resolver import AporiaRuntimeModeResolver
from aporia.infrastructure.event_fabric import AporiaEventFabric
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.infrastructure.product_lacuna_gateway import AporiaProductLacunaGateway
from aporia.infrastructure.perspective_projector import AporiaPerspectiveProjector


class AporiaEventApiEndpoint:
    """Handles HTTP event ingestion from agent runtime."""

    MAX_BODY_BYTES = 16384
    CANONICAL_TENANT_ROLES = ("owner", "full", "limited")
    CANONICAL_PRIVACY_SCOPES = (
        "GLOBAL_PUBLIC",
        "TENANT_PRIVATE",
        "USER_PRIVATE",
        "PLATFORM_INTERNAL",
    )

    @classmethod
    def handle_request(
        cls,
        method: str,
        headers: dict[str, str],
        raw_body: bytes | str,
        pdo: Any,
        environment: Any = None,
        secret: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Processes an incoming HTTP event ingestion request."""
        request_id = (
            headers.get("HTTP_X_REQUEST_ID")
            or headers.get("X-Request-Id")
            or headers.get("x-request-id")
            or secrets.token_hex(16)
        ).strip()

        try:
            if method.upper() != "POST":
                return cls._error("method_not_allowed", 405, request_id)

            body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
            if not body_bytes:
                return cls._error("aporia_event_body_invalid", 422, request_id)
            if len(body_bytes) > cls.MAX_BODY_BYTES:
                return cls._error("aporia_event_body_invalid", 413, request_id)

            try:
                input_data = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                return cls._error("aporia_event_json_invalid", 400, request_id)

            if not isinstance(input_data, dict):
                return cls._error("aporia_event_json_invalid", 400, request_id)

            bridge_secret = secret
            if not bridge_secret:
                for var_name in ("APORIA_BRIDGE_SECRET", "AGENT_BRIDGE_SECRET"):
                    if hasattr(environment, "getString"):
                        bridge_secret = environment.getString(var_name, "")
                    elif hasattr(environment, "get"):
                        bridge_secret = environment.get(var_name, "")
                    elif isinstance(environment, dict):
                        bridge_secret = environment.get(var_name, "")
                    if not bridge_secret:
                        bridge_secret = os.getenv(var_name, "")
                    if bridge_secret:
                        break

            bridge_secret = bridge_secret.strip() if bridge_secret else ""
            if not bridge_secret:
                raise RuntimeError("aporia_bridge_unavailable")

            auth_header = (
                headers.get("HTTP_AUTHORIZATION")
                or headers.get("Authorization")
                or headers.get("authorization")
                or ""
            ).strip()

            claims = cls._extract_claims(auth_header, bridge_secret)
            context = cls._verified_context(pdo, claims, input_data)
            controls = AporiaIndependentControlPlane(pdo, environment)

            if controls.any_engaged(int(context["tenant_id"]), [
                AporiaIndependentControlPlane.MEMORY_WRITES,
                AporiaIndependentControlPlane.ONTOLOGY,
            ]):
                return {
                    "data": {
                        "deduplicated": False,
                        "projected": False,
                        "recorded": False,
                        "reason_codes": ["aporia_write_kill_switch"],
                    },
                    "meta": {"request_id": request_id},
                    "code": 200,
                }, 200

            if AporiaRuntimeModeResolver.for_tenant(environment, pdo, int(context["tenant_id"])) == "disabled":
                return {
                    "data": {
                        "acknowledged": True,
                        "deduplicated": False,
                        "projected": False,
                        "recorded": False,
                        "reason_codes": ["aporia_disabled"],
                    },
                    "meta": {"request_id": request_id},
                    "code": 200,
                }, 200

            fabric = AporiaEventFabric(pdo, bridge_secret)
            result = fabric.ingest(context, input_data)
            projection_completed = False

            try:
                gateway = AporiaProductLacunaGateway(pdo, bridge_secret, controls)
                projector = AporiaPerspectiveProjector(pdo, gateway)
                proj = projector.project(int(context["tenant_id"]), int(result["event_row_id"]))
                projection_completed = bool(proj.get("completed"))
            except Exception:
                pass

            return {
                "data": {
                    "deduplicated": bool(result.get("deduplicated")),
                    "projected": projection_completed,
                },
                "meta": {"request_id": request_id},
                "code": 200,
            }, 200

        except RuntimeError as exc:
            code = str(exc)
            if code in ["aporia_token_required", "aporia_token_invalid", "aporia_token_expired"]:
                status = 401
            elif code in [
                "aporia_tenant_context_required",
                "aporia_tenant_forbidden",
                "aporia_session_forbidden",
                "aporia_task_forbidden",
                "runtime_session_invalid",
                "runtime_token_stale",
            ]:
                status = 403
            elif code in ["aporia_event_conflict", "aporia_event_sequence_conflict"]:
                status = 409
            elif code == "aporia_bridge_unavailable":
                status = 503
            else:
                status = 422
            return cls._error(code, status, request_id)
        except Exception as exc:
            status = 500
            err_code = "aporia_event_ingest_failed"
            if "database" in str(exc).lower() or "sqlite" in str(exc).lower() or "pdo" in str(exc).lower():
                status = 503
                err_code = "database_unavailable"
            return cls._error(err_code, status, request_id)

    @classmethod
    def _extract_claims(cls, auth_header: str, secret: str) -> dict[str, Any]:
        if not auth_header.startswith("Bearer "):
            raise RuntimeError("aporia_token_required")

        token = auth_header[7:].strip()
        try:
            decoded = jwt_decode(token, secret)
        except ValueError as exc:
            msg = str(exc)
            if "expired" in msg.lower():
                raise RuntimeError("aporia_token_expired")
            raise RuntimeError("aporia_token_invalid")
        except Exception:
            raise RuntimeError("aporia_token_invalid")

        iss = str(decoded.get("iss", "")).strip()
        aud = str(decoded.get("aud", "")).strip()
        if not iss or not aud:
            raise RuntimeError("aporia_token_invalid")

        if not str(decoded.get("jti", "")).strip():
            raise RuntimeError("aporia_token_invalid")

        cls._canonical_tenant_context(decoded)
        return decoded

    @classmethod
    def _verified_context(cls, pdo: Any, claims: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        tenant_context = cls._canonical_tenant_context(claims)
        if (
            claims.get("recovery") is not True
            or not hmac.compare_digest(str(claims.get("recovery_event_id", "")), str(input_data.get("event_id", "")))
            or not hmac.compare_digest(str(claims.get("recovery_canonical_sha256", "")), str(input_data.get("canonical_sha256", "")))
            or int(claims.get("recovery_journal_sequence", 0)) != int(input_data.get("journal_sequence", 0))
        ):
            raise RuntimeError("aporia_token_invalid")

        if not isinstance(input_data.get("tenant_id"), int) or input_data["tenant_id"] != tenant_context["tenant_id"]:
            raise RuntimeError("aporia_tenant_forbidden")

        session_id = str(claims.get("session_id", ""))
        agent_session_id = str(claims.get("agent_session_id", session_id))
        owner_user_id = int(claims.get("owner_user_id", 0))
        actor_user_id = int(claims.get("actor_user_id", 0))

        context = None
        try:
            stmt = pdo.prepare(
                """SELECT session.id, session.public_id, session.owner_user_id, session.actor_user_id,
                        session.agent_session_id, session.profile_id, session.workspace_profile_id, session.relationship_id
                 FROM assistant_runtime_sessions session
                 WHERE session.public_id = ? AND session.owner_user_id = ? AND session.actor_user_id = ?
                   AND session.agent_session_id = ? AND session.status IN ('active', 'archived')
                 LIMIT 1"""
            )
            stmt.execute([session_id, owner_user_id, actor_user_id, agent_session_id])
            context = stmt.fetch()
        except Exception:
            pass

        if not context:
            try:
                stmt = pdo.prepare(
                    """SELECT session.id, session.public_id, session.owner_user_id, session.actor_user_id,
                            session.profile_id, session.workspace_profile_id, session.relationship_id
                     FROM assistant_runtime_sessions session
                     WHERE session.public_id = ? AND session.owner_user_id = ? AND session.actor_user_id = ?
                       AND session.status IN ('active', 'archived')
                     LIMIT 1"""
                )
                stmt.execute([session_id, owner_user_id, actor_user_id])
                context = stmt.fetch()
            except Exception:
                pass

        if not context:
            raise RuntimeError("runtime_session_invalid")

        for field in ["profile_id", "workspace_profile_id", "relationship_id"]:
            claim_val = int(claims[field]) if field in claims and claims[field] is not None else None
            actual_val = int(context[field]) if field in context and context[field] is not None else None
            if claim_val != actual_val:
                raise RuntimeError("runtime_token_stale")

        res = dict(context)
        res["turn_id"] = str(claims.get("turn_id", "")).strip()
        res.update(tenant_context)
        return res

    @classmethod
    def _canonical_tenant_context(cls, claims: dict[str, Any]) -> dict[str, Any]:
        if (
            claims.get("tenant_context_status") != "resolved"
            or claims.get("tenant_context_source") != "canonical"
            or ("tenant_context_required" in claims and claims["tenant_context_required"] is not True)
        ):
            raise RuntimeError("aporia_tenant_context_required")

        tenant_id = cls._positive_integer_claim(claims, "tenant_id")
        membership_id = cls._positive_integer_claim(claims, "membership_id")
        cls._positive_integer_claim(claims, "owner_user_id")
        cls._positive_integer_claim(claims, "actor_user_id")

        role = claims.get("role")
        privacy_scope = claims.get("privacy_scope")
        context_revision = cls._positive_integer_claim(claims, "context_revision")

        if (
            not isinstance(role, str)
            or role not in cls.CANONICAL_TENANT_ROLES
            or not isinstance(privacy_scope, str)
            or privacy_scope not in cls.CANONICAL_PRIVACY_SCOPES
        ):
            raise RuntimeError("aporia_tenant_context_required")

        return {
            "tenant_id": tenant_id,
            "membership_id": membership_id,
            "role": role,
            "privacy_scope": privacy_scope,
            "context_revision": context_revision,
            "tenant_context_status": "resolved",
            "tenant_context_source": "canonical",
        }

    @classmethod
    def _positive_integer_claim(cls, claims: dict[str, Any], name: str) -> int:
        val = claims.get(name)
        if not isinstance(val, int) or val < 1:
            raise RuntimeError("aporia_tenant_context_required")
        return val

    @classmethod
    def claims(cls, secret: str, auth_header: str | None = None) -> dict[str, Any]:
        if auth_header is None:
            auth_header = os.environ.get("HTTP_AUTHORIZATION", "")
        return cls._extract_claims(auth_header, secret)

    @classmethod
    def verified_context(cls, pdo: Any, claims: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        return cls._verified_context(pdo, claims, input_data)

    @classmethod
    def verifiedContext(cls, pdo: Any, claims: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        return cls._verified_context(pdo, claims, input_data)

    @classmethod
    def _error(cls, code: str, status: int, request_id: str) -> tuple[dict[str, Any], int]:
        return {
            "error": {
                "code": code,
                "message": "Não foi possível registar o evento interno.",
            },
            "meta": {"request_id": request_id},
            "code": status,
        }, status
