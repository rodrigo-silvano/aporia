"""
Product Context Service for APORIA.
Augments user context with verified product state and manages exposures and outcomes.
"""

from __future__ import annotations
import re
import json
import hashlib
import hmac
from typing import Any

from aporia.infrastructure.guarded_policy import AporiaGuardedPolicy
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.infrastructure.product_state import AporiaProductStateRepository


class AporiaProductContextService:
    """Delivers read-only product context envelopes and tracks runtime outcomes."""

    def __init__(
        self,
        pdo: Any,
        secret: str,
        environment: str,
        controls: AporiaIndependentControlPlane,
    ) -> None:
        if not secret or secret.strip() == "":
            raise RuntimeError("aporia_product_context_secret_required")
        self.pdo = pdo
        self.secret = secret
        self.environment = environment
        self.controls = controls

    def augment(
        self,
        base: dict[str, Any],
        tenant_id: int,
        turn_id: str,
        mode: str,
        record_exposure: bool = True,
    ) -> dict[str, Any]:
        """Augments prompt context with product envelope if appropriate."""
        if mode not in ["advisory", "guarded_reversible"]:
            return base

        if tenant_id < 1 or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id):
            raise RuntimeError("aporia_product_context_invalid")

        snapshot = AporiaProductStateRepository(self.pdo).latest_snapshot(tenant_id)
        if snapshot is None:
            res = dict(base)
            res["aporia_fallback"] = {"reason_code": "product_snapshot_unavailable"}
            return res

        product = {
            "mode": mode,
            "snapshot_id": snapshot["snapshot_id"],
            "state_revision": snapshot["state_revision"],
            "policy_version": snapshot["policy_version"],
            "valid_until": snapshot["valid_until"],
            "context_envelope": self._deliverable_envelope(snapshot["envelope"]),
            "limitations": ["non_authoritative", "no_effect_permission", "no_chain_of_thought"],
            "schema_version": 1,
        }
        commitment = hashlib.sha256(self._json(product).encode("utf-8")).hexdigest()
        receipt = {
            "snapshot_id": snapshot["snapshot_id"],
            "state_revision": snapshot["state_revision"],
            "mode": mode,
            "envelope_commitment": commitment,
            "turn_ref": self._turn_ref(tenant_id, turn_id),
        }
        receipt["receipt_signature"] = self._signature(tenant_id, receipt)

        res = dict(base)
        if record_exposure and self._memory_writes_allowed(tenant_id):
            self._persist_exposure(tenant_id, receipt)
        else:
            res["aporia_delivery_receipt"] = receipt

        res["aporia_product"] = product
        return res

    def record_prepared_exposure(self, tenant_id: int, turn_id: str, receipt: dict[str, Any]) -> None:
        """Records an exposure that was prepared previously."""
        if (
            tenant_id < 1
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id)
            or not hmac.compare_digest(self._turn_ref(tenant_id, turn_id), str(receipt.get("turn_ref", "")))
            or not hmac.compare_digest(self._signature(tenant_id, receipt), str(receipt.get("receipt_signature", "")))
        ):
            raise RuntimeError("aporia_product_exposure_invalid")

        if not self._memory_writes_allowed(tenant_id):
            raise RuntimeError("aporia_product_memory_writes_disabled")

        self._persist_exposure(tenant_id, receipt)

    def record_outcome(
        self,
        tenant_id: int,
        turn_id: str,
        decision_commitment: str,
        latency_ms: int,
        cost_units: float,
        critical_regression: bool,
    ) -> dict[str, Any]:
        """Records the runtime outcome of an exposed turn."""
        if (
            tenant_id < 1
            or not re.match(r"^[A-Za-z0-9_-]{1,191}$", turn_id)
            or not re.match(r"^[0-9a-f]{64}$", decision_commitment)
            or latency_ms < 0
            or cost_units < 0.0
        ):
            raise RuntimeError("aporia_product_outcome_invalid")

        if not self._memory_writes_allowed(tenant_id):
            return {"recorded": False, "reason_codes": ["aporia_memory_writes_kill_switch"]}

        turn_ref = self._turn_ref(tenant_id, turn_id)
        exposure_stmt = self.pdo.prepare(
            "SELECT exposure_id, mode FROM aporia_product_context_exposures WHERE tenant_id = ? AND turn_ref = ? LIMIT 1"
        )
        exposure_stmt.execute([tenant_id, turn_ref])
        row = exposure_stmt.fetch()
        if not row:
            raise RuntimeError("aporia_product_exposure_unavailable")

        outcome_id = self._uuid(
            hashlib.sha256(f"{tenant_id}|{row['exposure_id']}|product-outcome".encode("utf-8")).hexdigest()
        )
        reasons = ["decision_commitment_observed", "cost_and_latency_observed"]
        if critical_regression:
            reasons.append("critical_regression_observed")

        insert_sql = (
            "INSERT OR IGNORE"
            if getattr(self.pdo, "getAttribute", lambda x: "sqlite")(None) == "sqlite"
            else "INSERT IGNORE"
        )
        stmt = self.pdo.prepare(
            f"""{insert_sql} INTO aporia_product_runtime_outcomes
                (outcome_id, tenant_id, exposure_id, decision_commitment, helpful, latency_ms,
                 cost_units, critical_regression, evaluability, reason_codes_json)
             VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'not_evaluable', ?)"""
        )
        stmt.execute([
            outcome_id,
            tenant_id,
            row["exposure_id"],
            decision_commitment,
            latency_ms,
            cost_units,
            1 if critical_regression else 0,
            self._json(reasons),
        ])

        if stmt.rowcount == 1 and critical_regression and str(row.get("mode")) == "guarded_reversible":
            AporiaGuardedPolicy(self.pdo, self.environment).stop_for_critical_regression(tenant_id)

        return {"recorded": True, "evaluability": "not_evaluable", "reason_codes": reasons}

    def record_feedback(self, tenant_id: int, turn_id: str, helpful: bool) -> dict[str, Any]:
        """Records explicit user feedback for an outcome."""
        if not self._memory_writes_allowed(tenant_id):
            return {"recorded": False, "reason_codes": ["aporia_memory_writes_kill_switch"]}

        turn_ref = self._turn_ref(tenant_id, turn_id)
        stmt = self.pdo.prepare(
            """SELECT outcome.id, outcome.helpful
             FROM aporia_product_runtime_outcomes outcome
             INNER JOIN aporia_product_context_exposures exposure
                ON exposure.tenant_id = outcome.tenant_id AND exposure.exposure_id = outcome.exposure_id
             WHERE outcome.tenant_id = ? AND exposure.turn_ref = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, turn_ref])
        row = stmt.fetch()
        if not row:
            raise RuntimeError("aporia_product_outcome_unavailable")

        if row.get("helpful") is not None and bool(row["helpful"]) != helpful:
            raise RuntimeError("aporia_product_feedback_conflict")

        reasons = ["decision_commitment_observed", "explicit_helpful_feedback" if helpful else "explicit_not_helpful_feedback"]
        update = self.pdo.prepare(
            """UPDATE aporia_product_runtime_outcomes
             SET helpful = ?, evaluability = 'observed', reason_codes_json = ?, updated_at = CURRENT_TIMESTAMP
             WHERE tenant_id = ? AND id = ? AND helpful IS NULL"""
        )
        update.execute([1 if helpful else 0, self._json(reasons), tenant_id, row["id"]])
        return {"recorded": True, "evaluability": "observed", "helpful": helpful, "reason_codes": reasons}

    def _persist_exposure(self, tenant_id: int, receipt: dict[str, Any]) -> None:
        if (
            not re.match(r"^[0-9a-f-]{36}$", str(receipt.get("snapshot_id", "")))
            or int(receipt.get("state_revision", -1)) < 0
            or str(receipt.get("mode", "")) not in ["advisory", "guarded_reversible"]
            or not re.match(r"^[0-9a-f]{64}$", str(receipt.get("envelope_commitment", "")))
            or not re.match(r"^[0-9a-f]{64}$", str(receipt.get("turn_ref", "")))
            or not re.match(r"^[0-9a-f]{64}$", str(receipt.get("receipt_signature", "")))
        ):
            raise RuntimeError("aporia_product_exposure_invalid")

        snapshot_stmt = self.pdo.prepare(
            "SELECT state_revision FROM aporia_product_state_snapshots WHERE tenant_id = ? AND snapshot_id = ? LIMIT 1"
        )
        snapshot_stmt.execute([tenant_id, receipt["snapshot_id"]])
        stored_revision = snapshot_stmt.fetchColumn(0)
        if stored_revision is False or stored_revision is None or int(stored_revision) != int(receipt["state_revision"]):
            raise RuntimeError("aporia_product_exposure_invalid")

        exposure_id = self._uuid(
            hashlib.sha256(f"{tenant_id}|{receipt['turn_ref']}|product-exposure".encode("utf-8")).hexdigest()
        )
        insert_sql = (
            "INSERT OR IGNORE"
            if getattr(self.pdo, "getAttribute", lambda x: "sqlite")(None) == "sqlite"
            else "INSERT IGNORE"
        )
        stmt = self.pdo.prepare(
            f"""{insert_sql} INTO aporia_product_context_exposures
                (exposure_id, tenant_id, turn_ref, snapshot_id, state_revision, mode, envelope_commitment, receipt_signature)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        stmt.execute([
            exposure_id,
            tenant_id,
            receipt["turn_ref"],
            receipt["snapshot_id"],
            receipt["state_revision"],
            receipt["mode"],
            receipt["envelope_commitment"],
            receipt["receipt_signature"],
        ])

    def _memory_writes_allowed(self, tenant_id: int) -> bool:
        return not self.controls.engaged(tenant_id, AporiaIndependentControlPlane.MEMORY_WRITES)

    def _deliverable_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        groups = [
            "confirmed_facts",
            "relevant_commitments",
            "verified_negative_outcomes",
            "active_constraints",
            "unresolved_conflicts",
            "uncertainty",
        ]
        deliverable: dict[str, Any] = {}
        selected = 0
        for group in groups:
            items = list(envelope.get(group, [])) if isinstance(envelope.get(group), list) else []
            slice_len = max(0, 24 - selected)
            deliverable[group] = items[:slice_len]
            selected += len(deliverable[group])

        rec = envelope.get("recommended_evidence")
        deliverable["recommended_evidence"] = list(rec)[:5] if isinstance(rec, list) else []
        
        prov = envelope.get("provenance_refs")
        if isinstance(prov, list):
            unique_prov: list[Any] = []
            for p in prov:
                if p not in unique_prov:
                    unique_prov.append(p)
            deliverable["provenance_refs"] = unique_prov[:24]
        else:
            deliverable["provenance_refs"] = []

        deliverable["limitations"] = ["non_authoritative", "no_effect_permission", "no_chain_of_thought"]
        deliverable["schema_version"] = 1
        return deliverable

    def _turn_ref(self, tenant_id: int, turn_id: str) -> str:
        msg = f"product-turn:{tenant_id}:{turn_id}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _signature(self, tenant_id: int, receipt: dict[str, Any]) -> str:
        r = dict(receipt)
        r.pop("receipt_signature", None)
        msg = f"{tenant_id}|{self._json(r)}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _uuid(self, hash_str: str) -> str:
        return f"{hash_str[0:8]}-{hash_str[8:12]}-4{hash_str[13:16]}-a{hash_str[17:20]}-{hash_str[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
