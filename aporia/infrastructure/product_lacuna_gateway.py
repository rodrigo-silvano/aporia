"""
Aporia Product Lacuna Gateway.
Manages one-shot capability preparation, execution, cryptographic binding,
state transformation, and secure destruction.
"""

from __future__ import annotations
import base64
from datetime import datetime, timezone, timedelta
import json
import os
import re
from typing import Any, Mapping
from aporia.crypto import canonical_json, sha256_hex, hmac_sha256_hex, hash_equals, simple_aead_encrypt, simple_aead_decrypt
from aporia.infrastructure.db import Connection
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane


class AporiaProductLacunaGateway:
    INSTRUMENTS = ("discard_alternative_v1", "focus_constraints_v1")
    ALLOWED_STATE_KEYS = ("hypotheses", "alternatives", "plans", "constraints", "uncertainty")

    def __init__(
        self,
        conn: Connection | Any,
        secret: str,
        controls: AporiaIndependentControlPlane | None = None,
    ) -> None:
        if len(secret) < 16:
            raise RuntimeError("aporia_product_lacuna_secret_required")
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.secret = secret
        self.controls = controls

    def prepare(
        self,
        tenant_id: int,
        session_ref: str,
        turn_ref: str,
        instrument: str,
        derived_state: Mapping[str, Any],
        ttl_seconds: int = 60,
    ) -> dict[str, Any]:
        self._validate_scope(tenant_id, session_ref, turn_ref, instrument)
        self._assert_enabled(tenant_id)
        self._assert_derived_state(derived_state)
        if ttl_seconds < 5 or ttl_seconds > 300:
            raise RuntimeError("aporia_product_lacuna_ttl_invalid")

        one_shot_id = self._uuid(os.urandom(32).hex())
        capability = os.urandom(32).hex()
        session_binding = self._binding("session", tenant_id, session_ref)
        turn_binding = self._binding("turn", tenant_id, turn_ref)

        aad = canonical_json({
            "tenant_id": tenant_id,
            "one_shot_id": one_shot_id,
            "session_binding": session_binding,
            "turn_binding": turn_binding,
            "instrument": instrument,
            "schema_version": 1,
        }).encode("utf-8")

        plaintext = canonical_json(derived_state).encode("utf-8")
        key = self._key(one_shot_id, session_binding, turn_binding, capability)
        nonce = os.urandom(24)
        ciphertext = simple_aead_encrypt(plaintext, aad, nonce, key)

        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")

        stmt = self.conn.prepare("""
            INSERT INTO aporia_product_lacuna_one_shots
            (one_shot_id, tenant_id, session_binding, turn_binding, instrument, capability_hash,
             nonce_base64, ciphertext_base64, input_commitment, output_commitment,
             schema_version, status, expires_at, consumed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 'ready', ?, NULL, ?, ?)
        """)
        stmt.execute([
            one_shot_id,
            tenant_id,
            session_binding,
            turn_binding,
            instrument,
            hmac_sha256_hex(self.secret, f"{tenant_id}|{one_shot_id}|{capability}"),
            base64.b64encode(nonce).decode("utf-8"),
            base64.b64encode(ciphertext).decode("utf-8"),
            sha256_hex(plaintext.decode("utf-8")),
            expires,
            now_str,
            now_str,
        ])

        return {
            "one_shot_id": one_shot_id,
            "capability_token": capability,
            "expires_at": expires,
            "schema_version": 1,
        }

    def consume(
        self,
        tenant_id: int,
        session_ref: str,
        turn_ref: str,
        one_shot_id: str,
        capability_token: str,
    ) -> dict[str, Any]:
        if tenant_id < 1 or not re.match(r"^[0-9a-f-]{36}$", one_shot_id) or len(capability_token) != 64:
            raise RuntimeError("aporia_product_lacuna_capability_invalid")

        self._assert_enabled(tenant_id)
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare(
                "SELECT * FROM aporia_product_lacuna_one_shots WHERE tenant_id = ? AND one_shot_id = ? LIMIT 1"
            )
            stmt.execute([tenant_id, one_shot_id])
            row = stmt.fetch()
            if not row:
                raise RuntimeError("aporia_product_lacuna_capability_invalid")
            if str(row.get("status")) != "ready":
                raise RuntimeError("aporia_product_lacuna_capability_consumed")

            now = datetime.now(timezone.utc)
            now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
            if str(row.get("expires_at", "")) <= now_str:
                self._expire_row(tenant_id, one_shot_id, str(row.get("input_commitment")), now)
                if started:
                    self.conn.commit()
                raise RuntimeError("aporia_product_lacuna_capability_expired")

            session_binding = self._binding("session", tenant_id, session_ref)
            turn_binding = self._binding("turn", tenant_id, turn_ref)
            expected_hash = hmac_sha256_hex(self.secret, f"{tenant_id}|{one_shot_id}|{capability_token}")

            if (
                not hash_equals(str(row.get("session_binding")), session_binding)
                or not hash_equals(str(row.get("turn_binding")), turn_binding)
                or not hash_equals(str(row.get("capability_hash")), expected_hash)
            ):
                raise RuntimeError("aporia_product_lacuna_capability_invalid")

            aad = canonical_json({
                "tenant_id": tenant_id,
                "one_shot_id": one_shot_id,
                "session_binding": session_binding,
                "turn_binding": turn_binding,
                "instrument": str(row.get("instrument")),
                "schema_version": 1,
            }).encode("utf-8")

            key = self._key(one_shot_id, session_binding, turn_binding, capability_token)
            ciphertext = base64.b64decode(str(row.get("ciphertext_base64", "")))
            nonce = base64.b64decode(str(row.get("nonce_base64", "")))

            try:
                decrypted = simple_aead_decrypt(ciphertext, aad, nonce, key)
            except Exception:
                raise RuntimeError("aporia_product_lacuna_decrypt_failed")

            if not hash_equals(str(row.get("input_commitment")), sha256_hex(decrypted.decode("utf-8"))):
                raise RuntimeError("aporia_product_lacuna_decrypt_failed")

            state = json.loads(decrypted.decode("utf-8"))
            if not isinstance(state, dict):
                raise RuntimeError("aporia_product_lacuna_state_invalid")

            output = self._transform(state, str(row.get("instrument")))
            output_commitment = sha256_hex(canonical_json(output))
            destruction_receipt = hmac_sha256_hex(
                self.secret,
                f"{tenant_id}|{one_shot_id}|{row.get('input_commitment')}|{output_commitment}|destroyed",
            )

            update_stmt = self.conn.prepare("""
                UPDATE aporia_product_lacuna_one_shots
                SET status = 'consumed', output_commitment = ?, destruction_receipt = ?,
                    ciphertext_base64 = '', nonce_base64 = '', consumed_at = ?, updated_at = ?
                WHERE tenant_id = ? AND one_shot_id = ? AND status = 'ready'
            """)
            update_stmt.execute([output_commitment, destruction_receipt, now_str, now_str, tenant_id, one_shot_id])
            if update_stmt.rowcount != 1:
                raise RuntimeError("aporia_product_lacuna_capability_consumed")

            if started:
                self.conn.commit()

            return {
                "result": output,
                "output_commitment": output_commitment,
                "destruction_receipt": destruction_receipt,
                "consumed": True,
            }
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def evaluate_safely(
        self,
        tenant_id: int,
        session_ref: str,
        turn_ref: str,
        instrument: str,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            prepared = self.prepare(tenant_id, session_ref, turn_ref, instrument, state)
            consumed = self.consume(tenant_id, session_ref, turn_ref, prepared["one_shot_id"], prepared["capability_token"])
            return {"available": True, **consumed}
        except Exception as exc:
            return {
                "available": False,
                "reason_code": "lacuna_safe_fallback",
                "error_class": exc.__class__.__name__,
            }

    def evaluateSafely(
        self,
        tenant_id: int,
        session_ref: str,
        turn_ref: str,
        instrument: str,
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.evaluate_safely(tenant_id, session_ref, turn_ref, instrument, state)

    def expire_ready(self) -> int:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        receipt = hmac_sha256_hex(self.secret, f"expired|{now_str}")
        stmt = self.conn.prepare("""
            UPDATE aporia_product_lacuna_one_shots
            SET status = 'expired', destruction_receipt = ?, ciphertext_base64 = '', nonce_base64 = '', updated_at = ?
            WHERE status = 'ready' AND expires_at <= ?
        """)
        stmt.execute([receipt, now_str, now_str])
        return stmt.rowcount

    def expireReady(self) -> int:
        return self.expire_ready()

    def _expire_row(self, tenant_id: int, one_shot_id: str, input_commitment: str, now: datetime) -> None:
        ts = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        receipt = hmac_sha256_hex(self.secret, f"{tenant_id}|{one_shot_id}|{input_commitment}|expired-destroyed")
        stmt = self.conn.prepare("""
            UPDATE aporia_product_lacuna_one_shots
            SET status = 'expired', destruction_receipt = ?, ciphertext_base64 = '', nonce_base64 = '', updated_at = ?
            WHERE tenant_id = ? AND one_shot_id = ? AND status = 'ready'
        """)
        stmt.execute([receipt, ts, tenant_id, one_shot_id])

    def _transform(self, state: dict[str, Any], instrument: str) -> dict[str, Any]:
        res = dict(state)
        if instrument == "discard_alternative_v1":
            alts = list(res.get("alternatives", []))
            if alts:
                alts.pop()
            res["alternatives"] = alts
            return res

        constraints = list(res.get("constraints", []))
        deduped = sorted(list(set(str(x) for x in constraints)))
        res["constraints"] = deduped
        res["hypotheses"] = []
        return res

    def _assert_derived_state(self, state: Mapping[str, Any]) -> None:
        if not state or len(state) > len(self.ALLOWED_STATE_KEYS) or set(state.keys()) - set(self.ALLOWED_STATE_KEYS):
            raise RuntimeError("aporia_product_lacuna_state_invalid")
        if len(canonical_json(state)) > 16384:
            raise RuntimeError("aporia_product_lacuna_state_invalid")
        for val in state.values():
            if not isinstance(val, list) or len(val) > 50:
                raise RuntimeError("aporia_product_lacuna_state_invalid")

    def _validate_scope(self, tenant_id: int, session_ref: str, turn_ref: str, instrument: str) -> None:
        if (
            tenant_id < 1
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", session_ref)
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_ref)
            or instrument not in self.INSTRUMENTS
        ):
            raise RuntimeError("aporia_product_lacuna_scope_invalid")

    def _assert_enabled(self, tenant_id: int) -> None:
        if self.controls and self.controls.any_engaged(tenant_id, [
            AporiaIndependentControlPlane.GLOBAL,
            AporiaIndependentControlPlane.TENANT,
            AporiaIndependentControlPlane.ONTOLOGY,
        ]):
            raise RuntimeError("aporia_product_lacuna_disabled")

    def _binding(self, kind: str, tenant_id: int, value: str) -> str:
        return hmac_sha256_hex(self.secret, f"{kind}|{tenant_id}|{value}")

    def _key(self, one_shot_id: str, session_binding: str, turn_binding: str, capability_token: str) -> bytes:
        raw_hex = hmac_sha256_hex(
            self.secret,
            f"lacuna-key|{one_shot_id}|{session_binding}|{turn_binding}|{capability_token}",
        )
        return bytes.fromhex(raw_hex)

    @staticmethod
    def _uuid(hash_str: str) -> str:
        return (
            hash_str[0:8]
            + "-"
            + hash_str[8:12]
            + "-4"
            + hash_str[13:16]
            + "-a"
            + hash_str[17:20]
            + "-"
            + hash_str[20:32]
        )
