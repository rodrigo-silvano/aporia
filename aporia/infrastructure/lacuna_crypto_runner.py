"""
Aporia Lacuna Cryptographic Runner.
Executes the isolated one-shot sidecar in a separate process over IPC pipes,
enforcing capability consumption, memory destruction, and cryptographic commits.
"""

from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence
from aporia.crypto import canonical_json, sha256_hex, hmac_sha256_hex, hash_equals
from aporia.infrastructure.db import Connection


class AporiaLacunaCryptographicRunner:
    PROTOCOL_VERSION = 1
    MAX_MESSAGE_BYTES = 32768
    TIMEOUT_SECONDS = 5

    def __init__(self, conn: Connection | Any, secret: str, sidecar_path: str | Path | None = None) -> None:
        if not secret.strip():
            raise ValueError("aporia_crypto_runner_configuration_invalid")
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        default_sidecar = Path(__file__).parents[2] / "experiments" / "aporia-lacuna" / "sidecar" / "one_shot.py"
        self.sidecar_path = Path(sidecar_path) if sidecar_path else default_sidecar
        if not self.sidecar_path.is_file():
            raise ValueError("aporia_crypto_runner_configuration_invalid")
        self.auth_secret = sha256_hex(f"aporia-lacuna-cryptographic-v1|{secret}")

    def execute_and_persist(
        self,
        tenant_id: int,
        run_row_id: int,
        run_id: str,
        instrument: str,
        state: Sequence[float],
        expected_output: Sequence[float],
        created_at: str,
    ) -> dict[str, Any]:
        if tenant_id < 1 or run_row_id < 1 or not run_id.strip() or instrument not in ("q_then_r_v2", "r_then_q_v2"):
            raise ValueError("aporia_crypto_runner_scope_invalid")

        result = self.execute(tenant_id, instrument, state)
        expected_commitment = sha256_hex(canonical_json(expected_output))
        if not hash_equals(expected_commitment, str(result["output_commitment"])):
            raise ValueError("aporia_crypto_output_mismatch")

        sql = """
            INSERT INTO aporia_lacuna_crypto_commits
            (tenant_id, run_id, latent_id, instrument, input_ciphertext, input_nonce, destroyed_ciphertext,
             destroyed_nonce, input_commitment, output_commitment, capability_hash, destruction_receipt,
             execution_status, process_exit_code, core_dumps_disabled, memory_lock_status, event_name,
             event_version, environment, stream, category, component, operation_id, actor_type, action_name,
             lifecycle_phase, outcome, reason_code, trace_id,
             schema_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'consumed', 0, 1, ?,
                    'aporia.lacuna.crypto.consume.succeeded', 1, 'production', 'system', 'audit',
                    'aporia-lacuna-sidecar', ?, 'worker', 'cryptographic_consume', 'succeeded',
                    'succeeded', NULL, ?, 1, ?, ?)
        """
        trace_id = sha256_hex(f"{tenant_id}|{run_id}|{instrument}|crypto-v1")
        stmt = self.conn.prepare(sql)
        stmt.execute([
            tenant_id,
            run_row_id,
            result["latent_id"],
            instrument,
            result["input_ciphertext"],
            result["input_nonce"],
            result["destroyed_ciphertext"],
            result["destroyed_nonce"],
            result["input_commitment"],
            result["output_commitment"],
            result["capability_commitment"],
            result["receipt_auth"],
            result["memory_lock_status"],
            run_id,
            trace_id,
            created_at,
            created_at,
        ])

        return {
            "latent_id": result["latent_id"],
            "instrument": instrument,
            "input_commitment": result["input_commitment"],
            "output_commitment": result["output_commitment"],
            "changed_dimensions": result["changed_dimensions"],
        }

    def executeAndPersist(
        self,
        tenant_id: int,
        run_row_id: int,
        run_id: str,
        instrument: str,
        state: Sequence[float],
        expected_output: Sequence[float],
        created_at: str,
    ) -> dict[str, Any]:
        return self.execute_and_persist(tenant_id, run_row_id, run_id, instrument, state, expected_output, created_at)

    def execute(self, tenant_id: int, instrument: str, state: Sequence[float]) -> dict[str, Any]:
        if tenant_id < 1 or instrument not in ("q_then_r_v2", "r_then_q_v2"):
            raise ValueError("aporia_crypto_runner_scope_invalid")

        self._assert_state(state)
        latent_id = self._uuid(os.urandom(32).hex())
        capability_token = os.urandom(32).hex()

        cmd = [sys.executable, str(self.sidecar_path)]
        env = dict(os.environ)
        env["APORIA_SIDECAR_SECRET"] = self.auth_secret

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
        except Exception:
            raise RuntimeError("aporia_crypto_process_start_failed")

        try:
            # 1. Read hello handshake
            hello_line = proc.stdout.readline()
            if not hello_line:
                raise RuntimeError("aporia_crypto_handshake_invalid")
            hello = json.loads(hello_line)
            if (
                hello.get("protocol") != self.PROTOCOL_VERSION
                or hello.get("status") != "hello"
                or hello.get("core_dumps_disabled") is not True
                or hello.get("memory_lock_status") not in ("locked", "unsupported")
            ):
                raise RuntimeError("aporia_crypto_handshake_invalid")

            challenge = str(hello.get("challenge", ""))
            if len(challenge) != 64:
                raise RuntimeError("aporia_crypto_handshake_invalid")

            # 2. Send prepare
            prepare = {
                "protocol": self.PROTOCOL_VERSION,
                "op": "prepare",
                "challenge": challenge,
                "tenant_id": tenant_id,
                "latent_id": latent_id,
                "instrument": instrument,
                "capability_token": capability_token,
                "state": [float(x) for x in state],
            }
            prepare["auth"] = hmac_sha256_hex(self.auth_secret, canonical_json(prepare))
            proc.stdin.write(canonical_json(prepare) + "\n")
            proc.stdin.flush()

            # 3. Read sealed
            sealed_line = proc.stdout.readline()
            if not sealed_line:
                raise RuntimeError("aporia_crypto_handshake_invalid")
            sealed = json.loads(sealed_line)
            if (
                sealed.get("protocol") != self.PROTOCOL_VERSION
                or sealed.get("status") != "sealed"
                or sealed.get("latent_id") != latent_id
                or sealed.get("instrument") != instrument
            ):
                raise RuntimeError("aporia_crypto_handshake_invalid")

            nonce = str(sealed.get("nonce", ""))
            ciphertext = str(sealed.get("ciphertext", ""))
            input_commitment = str(sealed.get("input_commitment", ""))
            capability_commitment = str(sealed.get("capability_commitment", ""))

            # 4. Send consume
            consume = {
                "protocol": self.PROTOCOL_VERSION,
                "op": "consume",
                "challenge": challenge,
                "tenant_id": tenant_id,
                "latent_id": latent_id,
                "instrument": instrument,
                "capability_token": capability_token,
                "nonce": nonce,
                "ciphertext": ciphertext,
            }
            consume["auth"] = hmac_sha256_hex(self.auth_secret, canonical_json(consume))
            proc.stdin.write(canonical_json(consume) + "\n")
            proc.stdin.flush()

            # 5. Read consumed receipt
            receipt_line = proc.stdout.readline()
            if not receipt_line:
                raise RuntimeError("aporia_crypto_handshake_invalid")
            receipt = json.loads(receipt_line)
            if (
                receipt.get("protocol") != self.PROTOCOL_VERSION
                or receipt.get("status") != "consumed"
                or receipt.get("tenant_id") != tenant_id
                or receipt.get("latent_id") != latent_id
                or receipt.get("instrument") != instrument
            ):
                raise RuntimeError("aporia_crypto_handshake_invalid")

            receipt_auth = receipt.get("receipt_auth", "")
            receipt_check = dict(receipt)
            receipt_check.pop("receipt_auth", None)
            expected_auth = hmac_sha256_hex(self.auth_secret, canonical_json(receipt_check))
            if not hash_equals(expected_auth, str(receipt_auth)):
                raise RuntimeError("aporia_crypto_handshake_invalid")

            proc.wait(timeout=self.TIMEOUT_SECONDS)

            return {
                "latent_id": latent_id,
                "instrument": instrument,
                "input_ciphertext": ciphertext,
                "input_nonce": nonce,
                "destroyed_ciphertext": str(receipt.get("destroyed_ciphertext", "")),
                "destroyed_nonce": str(receipt.get("destroyed_nonce", "")),
                "input_commitment": input_commitment,
                "output_commitment": str(receipt.get("output_commitment", "")),
                "capability_commitment": capability_commitment,
                "receipt_auth": receipt_auth,
                "memory_lock_status": str(receipt.get("memory_lock_status", "unsupported")),
                "changed_dimensions": int(receipt.get("changed_dimensions", 0)),
            }
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def _assert_state(self, state: Sequence[float]) -> None:
        if len(state) != 32:
            raise ValueError("aporia_crypto_state_invalid")
        for val in state:
            if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                raise ValueError("aporia_crypto_state_invalid")

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
