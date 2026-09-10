"""
Autobiography and Identity Data Export for APORIA.
Exports verified autobiography entries and identity state with strict chain validation.
"""

from __future__ import annotations
import json
from typing import Any, Callable


class AporiaAutobiographyExport:
    """Exports tenant autobiographical entries and identity snapshots with cryptographic verification."""

    def __init__(self, pdo: Any, chain_verifier: Callable[[int], bool]) -> None:
        self.pdo = pdo
        self.chain_verifier = chain_verifier

    def export_tenant(self, tenant_id: int) -> dict[str, Any]:
        """Exports the verified autobiographical chain for a tenant."""
        if tenant_id < 1:
            raise RuntimeError("aporia_autobiography_export_scope_invalid")

        if not self.chain_verifier(tenant_id):
            raise RuntimeError("aporia_autobiography_export_chain_invalid")

        return {
            "schema_version": 1,
            "chain_verified": True,
            "autobiography_entries": self._autobiography_entries(tenant_id),
            "identity_snapshots": self._identity_snapshots(tenant_id),
            "identity_commitments": self._identity_commitments(tenant_id),
            "commitment_evidence": self._commitment_evidence(tenant_id),
        }

    def exportTenant(self, tenant_id: int) -> dict[str, Any]:
        return self.export_tenant(tenant_id)

    def _autobiography_entries(self, tenant_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("aporia_autobiography_entries"):
            return []

        stmt = self.pdo.prepare(
            """SELECT entry_id, run_id, causal_event_id, episode_ref, entry_kind, evaluability,
                    ontology_before_commitment, ontology_after_commitment, transformation_commitment,
                    lost_distinction_hashes_json, excluded_future_hashes_json, irrecoverability,
                    causal_efficacy, autobiographic_time, confidence, reason_codes_json, policy_hash,
                    payload_hash, previous_signature, entry_signature, schema_version, created_at, updated_at
             FROM aporia_autobiography_entries WHERE tenant_id = ? ORDER BY id"""
        )
        stmt.execute([tenant_id])
        rows = stmt.fetchAll() or []
        
        results = []
        for row in rows:
            results.append({
                "entry_id": str(row["entry_id"]),
                "run_id": int(row["run_id"]),
                "causal_event_id": str(row["causal_event_id"]),
                "episode_ref": str(row["episode_ref"]),
                "entry_kind": str(row["entry_kind"]),
                "evaluability": str(row["evaluability"]),
                "ontology_before_commitment": str(row["ontology_before_commitment"]),
                "ontology_after_commitment": row["ontology_after_commitment"],
                "transformation_commitment": str(row["transformation_commitment"]),
                "lost_distinction_hashes": self._decode_strings(str(row["lost_distinction_hashes_json"])),
                "excluded_future_hashes": self._decode_strings(str(row["excluded_future_hashes_json"])),
                "irrecoverability": self._nullable_float(row["irrecoverability"]),
                "causal_efficacy": self._nullable_float(row["causal_efficacy"]),
                "autobiographic_time": int(row["autobiographic_time"]) if row["autobiographic_time"] is not None else None,
                "confidence": self._nullable_float(row["confidence"]),
                "reason_codes": self._decode_strings(str(row["reason_codes_json"])),
                "policy_hash": str(row["policy_hash"]),
                "payload_hash": str(row["payload_hash"]),
                "previous_signature": str(row["previous_signature"]),
                "entry_signature": str(row["entry_signature"]),
                "schema_version": int(row["schema_version"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            })
        return results

    def _identity_snapshots(self, tenant_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("aporia_identity_snapshots"):
            return []

        stmt = self.pdo.prepare(
            """SELECT snapshot_id, autobiography_entry_id, graph_root, behavior_root, commitment_root,
                    autobiography_root, ontology_root, graph_count, behavior_count, commitment_count,
                    autobiography_count, ontology_count, fingerprint, continuity_score, model_dependency,
                    previous_signature, snapshot_signature, schema_version, created_at, updated_at
             FROM aporia_identity_snapshots WHERE tenant_id = ? ORDER BY id"""
        )
        stmt.execute([tenant_id])
        rows = stmt.fetchAll() or []
        
        results = []
        for row in rows:
            results.append({
                "snapshot_id": str(row["snapshot_id"]),
                "autobiography_entry_id": str(row["autobiography_entry_id"]),
                "graph_root": str(row["graph_root"]),
                "behavior_root": str(row["behavior_root"]),
                "commitment_root": str(row["commitment_root"]),
                "autobiography_root": str(row["autobiography_root"]),
                "ontology_root": str(row["ontology_root"]),
                "graph_count": int(row["graph_count"]),
                "behavior_count": int(row["behavior_count"]),
                "commitment_count": int(row["commitment_count"]),
                "autobiography_count": int(row["autobiography_count"]),
                "ontology_count": int(row["ontology_count"]),
                "fingerprint": str(row["fingerprint"]),
                "continuity_score": float(row["continuity_score"]),
                "model_dependency": str(row["model_dependency"]),
                "previous_signature": str(row["previous_signature"]),
                "snapshot_signature": str(row["snapshot_signature"]),
                "schema_version": int(row["schema_version"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            })
        return results

    def _identity_commitments(self, tenant_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("aporia_identity_commitments"):
            return []

        stmt = self.pdo.prepare(
            """SELECT commitment_id, commitment_key, commitment_kind, status, evidence_count,
                    first_causal_event_id, last_causal_event_id, committed_at, schema_version,
                    created_at, updated_at
             FROM aporia_identity_commitments WHERE tenant_id = ? ORDER BY id"""
        )
        stmt.execute([tenant_id])
        rows = stmt.fetchAll() or []
        
        results = []
        for row in rows:
            results.append({
                "commitment_id": str(row["commitment_id"]),
                "commitment_key": str(row["commitment_key"]),
                "commitment_kind": str(row["commitment_kind"]),
                "status": str(row["status"]),
                "evidence_count": int(row["evidence_count"]),
                "first_causal_event_id": str(row["first_causal_event_id"]),
                "last_causal_event_id": str(row["last_causal_event_id"]),
                "committed_at": row["committed_at"],
                "schema_version": int(row["schema_version"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            })
        return results

    def _commitment_evidence(self, tenant_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("aporia_identity_commitment_evidence") or not self._table_exists("aporia_identity_commitments"):
            return []

        stmt = self.pdo.prepare(
            """SELECT evidence.evidence_id, commitment.commitment_id AS commitment_uuid,
                    evidence.autobiography_entry_id, evidence.causal_event_id, evidence.evidence_ordinal,
                    evidence.resulting_status, evidence.reason_code, evidence.schema_version,
                    evidence.created_at, evidence.updated_at
             FROM aporia_identity_commitment_evidence evidence
             INNER JOIN aporia_identity_commitments commitment
                ON commitment.tenant_id = evidence.tenant_id AND commitment.id = evidence.commitment_id
             WHERE evidence.tenant_id = ? ORDER BY evidence.id"""
        )
        stmt.execute([tenant_id])
        rows = stmt.fetchAll() or []
        
        results = []
        for row in rows:
            results.append({
                "evidence_id": str(row["evidence_id"]),
                "commitment_id": str(row["commitment_uuid"]),
                "autobiography_entry_id": str(row["autobiography_entry_id"]),
                "causal_event_id": str(row["causal_event_id"]),
                "evidence_ordinal": int(row["evidence_ordinal"]),
                "resulting_status": str(row["resulting_status"]),
                "reason_code": str(row["reason_code"]),
                "schema_version": int(row["schema_version"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            })
        return results

    def _decode_strings(self, value: str) -> list[str]:
        try:
            decoded = json.loads(value)
        except Exception:
            raise RuntimeError("aporia_autobiography_export_data_invalid")

        if not isinstance(decoded, list):
            raise RuntimeError("aporia_autobiography_export_data_invalid")

        for item in decoded:
            if not isinstance(item, str) or len(item) > 160:
                raise RuntimeError("aporia_autobiography_export_data_invalid")

        return decoded

    def _nullable_float(self, value: Any) -> float | None:
        return float(value) if value is not None else None

    def _table_exists(self, table: str) -> bool:
        driver = getattr(self.pdo, "getAttribute", lambda x: "sqlite")(None)
        if driver == "sqlite":
            stmt = self.pdo.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1")
            stmt.execute([table])
            val = stmt.fetchColumn(0)
            return val is not False and val is not None
        else:
            stmt = self.pdo.prepare("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? LIMIT 1")
            stmt.execute([table])
            val = stmt.fetchColumn(0)
            return val is not False and val is not None
