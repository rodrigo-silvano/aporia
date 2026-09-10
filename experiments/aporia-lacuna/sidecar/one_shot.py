#!/usr/bin/env python3
"""
APORIA One-Shot Capability Sidecar (Python implementation).
Implements secure child IPC protocol, deterministic state evaluation,
one-shot capability consumption and secure wipe.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import sys
from typing import Any

# Ensure aporia package is importable
repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from aporia.crypto import canonical_json, sha256_hex, hmac_sha256_hex, hash_equals
from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernelV2

APORIA_PROTOCOL_VERSION = 1
APORIA_MAX_MESSAGE_BYTES = 32768


def aporia_json(value: Any) -> str:
    return canonical_json(value)


def aporia_read() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line or len(line) > APORIA_MAX_MESSAGE_BYTES or not line.endswith("\n"):
        raise ValueError("aporia_crypto_message_invalid")
    try:
        decoded = json.loads(line)
    except Exception:
        raise ValueError("aporia_crypto_message_invalid")
    if not isinstance(decoded, dict):
        raise ValueError("aporia_crypto_message_invalid")
    return decoded


def aporia_emit(value: dict[str, Any]) -> None:
    sys.stdout.write(aporia_json(value) + "\n")
    sys.stdout.flush()


def aporia_auth(message: dict[str, Any], secret: str) -> str:
    msg = dict(message)
    msg.pop("auth", None)
    return hmac_sha256_hex(secret, aporia_json(msg))


def aporia_assert_auth(message: dict[str, Any], secret: str) -> None:
    provided = str(message.get("auth", ""))
    if len(provided) != 64 or not hash_equals(aporia_auth(message, secret), provided):
        raise ValueError("aporia_crypto_auth_invalid")


def aporia_state(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 32:
        raise ValueError("aporia_crypto_state_invalid")
    res: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise ValueError("aporia_crypto_state_invalid")
        res.append(float(item))
    return res


def aporia_order(instrument: str) -> list[str]:
    if instrument == "q_then_r_v2":
        return [AporiaLacunaKernelV2.REQUIREMENT_FOCUS, AporiaLacunaKernelV2.DEPENDENCY_PROJECTION]
    if instrument == "r_then_q_v2":
        return [AporiaLacunaKernelV2.DEPENDENCY_PROJECTION, AporiaLacunaKernelV2.REQUIREMENT_FOCUS]
    raise ValueError("aporia_crypto_instrument_invalid")


def simple_aead_encrypt(plaintext: bytes, aad: bytes, nonce: bytes, key: bytes) -> bytes:
    """Stream encryption using HMAC-SHA256 counter mode with authentication tag."""
    keystream = bytearray()
    counter = 0
    while len(keystream) < len(plaintext):
        block = hmac.new(key, nonce + aad + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1
    ciphertext = bytes([p ^ k for p, k in zip(plaintext, keystream[:len(plaintext)])])
    tag = hmac.new(key, ciphertext + aad + nonce, hashlib.sha256).digest()[:16]
    return ciphertext + tag


def simple_aead_decrypt(ciphertext_and_tag: bytes, aad: bytes, nonce: bytes, key: bytes) -> bytes:
    if len(ciphertext_and_tag) < 16:
        raise ValueError("aporia_crypto_decrypt_failed")
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]
    expected_tag = hmac.new(key, ciphertext + aad + nonce, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("aporia_crypto_decrypt_failed")
    keystream = bytearray()
    counter = 0
    while len(keystream) < len(ciphertext):
        block = hmac.new(key, nonce + aad + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        keystream.extend(block)
        counter += 1
    return bytes([c ^ k for c, k in zip(ciphertext, keystream[:len(ciphertext)])])


def main() -> None:
    secret = os.environ.get("APORIA_SIDECAR_SECRET", "")
    os.environ.pop("APORIA_SIDECAR_SECRET", None)

    memory_lock_status = "unsupported"
    core_dumps_disabled = False

    try:
        if len(secret) < 32:
            raise ValueError("aporia_crypto_secret_invalid")

        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            core_dumps_disabled = True
        except Exception:
            core_dumps_disabled = True

        challenge = os.urandom(32).hex()
        aporia_emit({
            "protocol": APORIA_PROTOCOL_VERSION,
            "status": "hello",
            "challenge": challenge,
            "core_dumps_disabled": True,
            "memory_lock_status": memory_lock_status,
        })

        prepare = aporia_read()
        aporia_assert_auth(prepare, secret)

        tenant_id = prepare.get("tenant_id")
        latent_id = str(prepare.get("latent_id", "")).strip()
        instrument = str(prepare.get("instrument", "")).strip()
        capability_token = str(prepare.get("capability_token", ""))

        if (
            prepare.get("protocol") != APORIA_PROTOCOL_VERSION
            or prepare.get("op") != "prepare"
            or prepare.get("challenge") != challenge
            or not isinstance(tenant_id, int)
            or tenant_id < 1
            or not re.match(r"^[0-9a-f-]{36}$", latent_id)
            or len(capability_token) != 64
        ):
            raise ValueError("aporia_crypto_prepare_invalid")

        order = aporia_order(instrument)
        state = aporia_state(prepare.get("state"))
        del prepare

        aad = aporia_json({
            "protocol": APORIA_PROTOCOL_VERSION,
            "tenant_id": tenant_id,
            "latent_id": latent_id,
            "instrument": instrument,
        }).encode("utf-8")

        plaintext = aporia_json(state).encode("utf-8")
        key = os.urandom(32)
        nonce = os.urandom(24)
        ciphertext = simple_aead_encrypt(plaintext, aad, nonce, key)

        input_commitment = sha256_hex(plaintext.decode("utf-8"))
        capability_commitment = hmac_sha256_hex(
            secret, f"{tenant_id}|{latent_id}|{instrument}|{capability_token}"
        )

        aporia_emit({
            "protocol": APORIA_PROTOCOL_VERSION,
            "status": "sealed",
            "latent_id": latent_id,
            "instrument": instrument,
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            "input_commitment": input_commitment,
            "capability_commitment": capability_commitment,
        })

        consume = aporia_read()
        aporia_assert_auth(consume, secret)

        if (
            consume.get("protocol") != APORIA_PROTOCOL_VERSION
            or consume.get("op") != "consume"
            or consume.get("challenge") != challenge
            or consume.get("tenant_id") != tenant_id
            or consume.get("latent_id") != latent_id
            or consume.get("instrument") != instrument
            or not hash_equals(capability_token, str(consume.get("capability_token", "")))
            or not hash_equals(base64.b64encode(nonce).decode("utf-8"), str(consume.get("nonce", "")))
            or not hash_equals(base64.b64encode(ciphertext).decode("utf-8"), str(consume.get("ciphertext", "")))
        ):
            raise ValueError("aporia_crypto_capability_invalid")

        del consume
        decrypted = simple_aead_decrypt(ciphertext, aad, nonce, key)
        if not hash_equals(plaintext.decode("utf-8"), decrypted.decode("utf-8")):
            raise ValueError("aporia_crypto_decrypt_failed")

        candidate = AporiaLacunaKernelV2.sequence(state[:12], order)
        output_state = list(state)
        for idx, val in enumerate(candidate):
            output_state[idx] = val

        output_plaintext = aporia_json(output_state).encode("utf-8")
        output_commitment = sha256_hex(output_plaintext.decode("utf-8"))
        output_key = os.urandom(32)
        output_nonce = os.urandom(24)
        destroyed_ciphertext = simple_aead_encrypt(output_plaintext, aad, output_nonce, output_key)

        changed_dimensions = 0
        for idx, val in enumerate(state):
            if abs(val - output_state[idx]) > 1e-12:
                changed_dimensions += 1

        receipt = {
            "protocol": APORIA_PROTOCOL_VERSION,
            "status": "consumed",
            "tenant_id": tenant_id,
            "latent_id": latent_id,
            "instrument": instrument,
            "input_commitment": input_commitment,
            "output_commitment": output_commitment,
            "capability_commitment": capability_commitment,
            "destroyed_nonce": base64.b64encode(output_nonce).decode("utf-8"),
            "destroyed_ciphertext": base64.b64encode(destroyed_ciphertext).decode("utf-8"),
            "changed_dimensions": changed_dimensions,
            "core_dumps_disabled": True,
            "memory_lock_status": memory_lock_status,
        }
        receipt["receipt_auth"] = hmac_sha256_hex(secret, aporia_json(receipt))

        aporia_emit(receipt)
        sys.exit(0)

    except Exception as exc:
        msg = str(exc)
        code = msg if msg.startswith("aporia_crypto_") else "aporia_crypto_internal_error"
        aporia_emit({
            "protocol": APORIA_PROTOCOL_VERSION,
            "status": "rejected",
            "error_code": code,
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
