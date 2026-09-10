"""
Cryptographic and deterministic canonical serialization primitives for APORIA.
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import math
import struct
import time
from typing import Any, Mapping, Sequence


def canonical_json(value: Any) -> str:
    """
    Deterministically encode value to canonical JSON string matching
    AporiaRequestIntegrity::canonicalJson specification.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request_integrity_number_invalid")
        if value == 0.0:
            return "0"
        if math.floor(value) == value:
            return f"{value:.0f}"
        rendered = f"{value:.15g}".lower()
        if "e" not in rendered:
            return rendered
        mantissa, exp = rendered.split("e", 1)
        return f"{mantissa}e{int(exp)}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        sorted_keys = sorted(str(k) for k in value.keys())
        items = [f"{canonical_json(k)}:{canonical_json(value[k])}" for k in sorted_keys]
        return "{" + ",".join(items) + "}"
    raise TypeError("request_integrity_value_unsupported")


def canonicalize_data(value: Any) -> Any:
    """
    Recursively sort dictionaries to canonical order.
    """
    if isinstance(value, Mapping):
        return {k: canonicalize_data(value[k]) for k in sorted(value.keys())}
    if isinstance(value, (list, tuple)):
        return [canonicalize_data(item) for item in value]
    return value


def sha256_hex(data: str | bytes) -> str:
    """Compute SHA-256 digest in hex."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hmac_sha256_hex(key: str | bytes, data: str | bytes) -> str:
    """Compute HMAC-SHA256 digest in hex."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def hash_equals(a: str, b: str) -> bool:
    """Constant-time comparison."""
    return hmac.compare_digest(str(a), str(b))


def opaque_identifier(secret: str, value: str) -> str:
    """Compute opaque identifier using HMAC-SHA256."""
    if not secret:
        raise ValueError("request_integrity_hmac_secret_required")
    return hmac_sha256_hex(secret, value)


def wire_part(label: str, value: str) -> bytes:
    """
    Pack binary wire part:
    4-byte big-endian length of label + label bytes +
    8-byte big-endian length of value + value bytes.
    """
    label_bytes = label.encode("utf-8")
    value_bytes = value.encode("utf-8")
    return (
        struct.pack(">I", len(label_bytes))
        + label_bytes
        + struct.pack(">Q", len(value_bytes))
        + value_bytes
    )


def textual_content(value: Any) -> str:
    """Extract textual content from message payload."""
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return ""
    text = ""
    for item in value:
        if isinstance(item, str):
            text += item
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            text += item["text"]
    return text


def prompt_wire_bytes(instructions: str | None, messages: Sequence[Any]) -> bytes:
    """Compute binary wire bytes for prompt."""
    wire = wire_part("instructions", instructions or "")
    for idx, message in enumerate(messages):
        item = message if isinstance(message, Mapping) else {}
        wire += wire_part(f"message[{idx}].role", str(item.get("role", "")))
        wire += wire_part(f"message[{idx}].content", textual_content(item.get("content")))
    return wire


def prompt_wire_hash(instructions: str | None, messages: Sequence[Any]) -> str:
    """Compute SHA-256 hash of wire bytes."""
    return sha256_hex(prompt_wire_bytes(instructions, messages))


# =====================================================================
# Pure Python JWT implementation (RFC 7519) for HS256
# =====================================================================

def _b64_url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_url_decode(data: str) -> bytes:
    padding = (4 - len(data) % 4) % 4
    return base64.urlsafe_b64decode(data + "=" * padding)


def jwt_encode(payload: Mapping[str, Any], secret: str, algorithm: str = "HS256") -> str:
    """Encode payload to JWT using HS256."""
    if algorithm != "HS256":
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    header = {"typ": "JWT", "alg": "HS256"}
    header_b64 = _b64_url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64_url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64_url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def jwt_decode(token: str, secret: str, algorithms: Sequence[str] = ("HS256",), verify_exp: bool = True) -> dict[str, Any]:
    """Decode and verify JWT token using HS256."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("aporia_token_invalid")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header_bytes = _b64_url_decode(header_b64)
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception:
        raise ValueError("aporia_token_invalid")

    if header.get("alg") not in algorithms:
        raise ValueError("aporia_token_invalid")

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        given_sig = _b64_url_decode(sig_b64)
    except Exception:
        raise ValueError("aporia_token_invalid")

    if not hmac.compare_digest(expected_sig, given_sig):
        raise ValueError("aporia_token_invalid")

    try:
        payload_bytes = _b64_url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise ValueError("aporia_token_invalid")

    if verify_exp and "exp" in payload:
        exp = payload["exp"]
        if isinstance(exp, (int, float)) and exp < time.time():
            raise ValueError("aporia_token_expired")

    return payload


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
    """Stream decryption using HMAC-SHA256 counter mode with authentication tag verification."""
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

