"""Identity continuity and identity ledger for Aporia."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any

from aporia.crypto import canonical_json
from aporia.infrastructure.db import Connection


class AporiaIdentityContinuity:
    COMPONENTS = ["graph", "behavior", "commitment", "autobiography", "ontology"]
    SCORED_COMPONENTS = ["graph", "behavior", "commitment", "autobiography"]
    STABILITY_PRIOR = 8

    def fingerprint(self, state: dict[str, Any]) -> dict[str, Any]:
        norm_state = self.normalize(state)
        roots = {}
        counts = {}
        for component in self.COMPONENTS:
            c_json = self._json(norm_state[component])
            roots[component] = hashlib.sha256(c_json.encode("utf-8")).hexdigest()
            counts[component] = len(norm_state[component])

        payload = {
            "version": 1,
            "roots": roots,
            "counts": counts,
        }
        fp = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
        return {
            "fingerprint": fp,
            "roots": roots,
            "counts": counts,
            "state": norm_state,
            "model_dependency": "none",
            "version": 1,
        }

    def similarity(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        norm_left = self.normalize(left)
        norm_right = self.normalize(right)
        components = {}
        for component in self.SCORED_COMPONENTS:
            l_set = set(norm_left[component])
            r_set = set(norm_right[component])
            intersection = len(l_set & r_set)
            union = len(l_set | r_set)
            components[component] = (intersection + self.STABILITY_PRIOR) / (union + self.STABILITY_PRIOR)

        score = sum(components.values()) / len(components)
        return {
            "score": score,
            "components": components,
            "weights": {c: 0.25 for c in self.SCORED_COMPONENTS},
            "ontology_changed": norm_left["ontology"] != norm_right["ontology"],
            "version": 1,
        }

    def advance(self, state: dict[str, Any], component: str, commitment: str) -> dict[str, Any]:
        norm_state = self.normalize(state)
        if component not in self.COMPONENTS:
            raise RuntimeError("aporia_identity_component_invalid")
        self._assert_commitment(commitment)
        norm_state[component].append(commitment.lower())
        return self.normalize(norm_state)

    def fork(self, state: dict[str, Any]) -> dict[str, Any]:
        return self.normalize(state)

    def merge(self, base: dict[str, Any], left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        norm_base = self.normalize(base)
        norm_left = self.normalize(left)
        norm_right = self.normalize(right)

        merged = {}
        divergences = {}
        for component in self.COMPONENTS:
            b_set = set(norm_base[component])
            l_set = set(norm_left[component])
            r_set = set(norm_right[component])

            left_only = sorted(list(l_set - b_set - r_set))
            right_only = sorted(list(r_set - b_set - l_set))
            all_items = sorted(list(b_set | l_set | r_set))

            merged[component] = all_items
            divergences[component] = {
                "left_only": left_only,
                "right_only": right_only,
                "conflict": len(left_only) > 0 and len(right_only) > 0,
            }

        div_json = self._json(divergences)
        div_comm = hashlib.sha256(div_json.encode("utf-8")).hexdigest()
        return {
            "state": merged,
            "divergences": divergences,
            "divergence_commitment": div_comm,
            "version": 1,
        }

    def normalize(self, state: dict[str, Any]) -> dict[str, list[str]]:
        if not isinstance(state, dict):
            raise RuntimeError("aporia_identity_state_invalid")
        keys = sorted(list(state.keys()))
        expected = sorted(self.COMPONENTS)
        if keys != expected:
            raise RuntimeError("aporia_identity_state_invalid")

        normalized = {}
        for component in self.COMPONENTS:
            val = state.get(component)
            if not isinstance(val, (list, tuple)):
                raise RuntimeError("aporia_identity_state_invalid")
            items = []
            for item in val:
                if not isinstance(item, str):
                    raise RuntimeError("aporia_identity_state_invalid")
                self._assert_commitment(item)
                items.append(item.lower())
            unique_items = sorted(list(set(items)))
            normalized[component] = unique_items
        return normalized

    def _assert_commitment(self, commitment: str) -> None:
        if not re.match(r"^[0-9a-f]{64}$", commitment, re.IGNORECASE):
            raise RuntimeError("aporia_identity_commitment_invalid")

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class AporiaIdentityLedger:
    ZERO_SIGNATURE = "0000000000000000000000000000000000000000000000000000000000000000"
    COMMITMENT_THRESHOLD = 3

    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_identity_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def record_snapshot(self, tenant_id: int, autobiography_entry_id: str, created_at: str) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_identity_transaction_required")
        if tenant_id < 1 or not re.match(r"^[0-9a-f-]{36}$", autobiography_entry_id, re.IGNORECASE) or not created_at:
            raise RuntimeError("aporia_identity_scope_invalid")

        entry_stmt = self.conn.prepare(
            "SELECT entry_id, causal_event_id, policy_hash FROM aporia_autobiography_entries WHERE tenant_id = ? AND entry_id = ? LIMIT 1"
        )
        entry_stmt.execute([tenant_id, autobiography_entry_id])
        entry = entry_stmt.fetch()
        if not entry:
            raise RuntimeError("aporia_identity_autobiography_unavailable")

        self._ensure_head(tenant_id, created_at)
        head_stmt = self.conn.prepare(
            "SELECT last_signature, snapshot_count FROM aporia_identity_heads WHERE tenant_id = ? LIMIT 1"
        )
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not head:
            raise RuntimeError("aporia_identity_head_unavailable")

        existing = self.conn.prepare(
            "SELECT snapshot_id, fingerprint, snapshot_signature FROM aporia_identity_snapshots WHERE tenant_id = ? AND autobiography_entry_id = ? LIMIT 1"
        )
        existing.execute([tenant_id, autobiography_entry_id])
        snapshot = existing.fetch()
        if snapshot:
            return {
                "snapshot_id": str(snapshot["snapshot_id"]),
                "fingerprint": str(snapshot["fingerprint"]),
                "snapshot_signature": str(snapshot["snapshot_signature"]),
                "deduplicated": True,
            }

        continuity = AporiaIdentityContinuity()
        state = self._state(tenant_id)
        fp_data = continuity.fingerprint(state)
        prev = self._previous_state(tenant_id)
        similarity = 1.0 if prev is None else float(continuity.similarity(prev, state)["score"])
        prev_sig = str(head["last_signature"])
        snap_hash = hashlib.sha256(f"{tenant_id}|{autobiography_entry_id}|identity-v1".encode("utf-8")).hexdigest()
        snapshot_id = self._uuid(snap_hash)
        snapshot_sig = hmac.new(
            self.secret.encode("utf-8"),
            f"{tenant_id}|{autobiography_entry_id}|{prev_sig}|{fp_data['fingerprint']}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        op_id = hashlib.sha256(f"{tenant_id}|{snapshot_id}|identity-snapshot-v1".encode("utf-8")).hexdigest()

        insert_stmt = self.conn.prepare(
            """INSERT INTO aporia_identity_snapshots
             (snapshot_id, tenant_id, autobiography_entry_id, graph_commitments_json,
              behavior_commitments_json, commitment_commitments_json, autobiography_commitments_json,
              ontology_commitments_json, graph_root, behavior_root, commitment_root, autobiography_root,
              ontology_root, graph_count, behavior_count, commitment_count, autobiography_count,
              ontology_count, fingerprint, continuity_score, model_dependency, previous_signature,
              snapshot_signature, event_name, event_version, environment, stream, category, component,
              operation_id, actor_type, action_name, lifecycle_phase, outcome, reason_code,
              schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', ?, ?,
                     'aporia.identity.snapshot.recorded', 1, ?, 'system', 'audit', 'aporia-identity-ledger',
                     ?, 'worker', 'record_identity_snapshot', 'succeeded', 'succeeded', NULL, 1, ?, ?)"""
        )
        insert_stmt.execute([
            snapshot_id,
            tenant_id,
            autobiography_entry_id,
            self._json(state["graph"]),
            self._json(state["behavior"]),
            self._json(state["commitment"]),
            self._json(state["autobiography"]),
            self._json(state["ontology"]),
            fp_data["roots"]["graph"],
            fp_data["roots"]["behavior"],
            fp_data["roots"]["commitment"],
            fp_data["roots"]["autobiography"],
            fp_data["roots"]["ontology"],
            fp_data["counts"]["graph"],
            fp_data["counts"]["behavior"],
            fp_data["counts"]["commitment"],
            fp_data["counts"]["autobiography"],
            fp_data["counts"]["ontology"],
            fp_data["fingerprint"],
            f"{similarity:.12f}",
            prev_sig,
            snapshot_sig,
            self._environment(),
            op_id,
            created_at,
            created_at,
        ])

        update_stmt = self.conn.prepare(
            "UPDATE aporia_identity_heads SET last_signature = ?, snapshot_count = ?, updated_at = ? WHERE tenant_id = ? AND last_signature = ? AND snapshot_count = ?"
        )
        update_stmt.execute([
            snapshot_sig,
            int(head["snapshot_count"]) + 1,
            created_at,
            tenant_id,
            prev_sig,
            int(head["snapshot_count"]),
        ])
        if update_stmt.row_count != 1:
            raise RuntimeError("aporia_identity_head_conflict")

        commitment = self._record_slow_commitment(
            tenant_id,
            autobiography_entry_id,
            str(entry["causal_event_id"]),
            str(entry["policy_hash"]),
            created_at,
        )

        return {
            "snapshot_id": snapshot_id,
            "fingerprint": fp_data["fingerprint"],
            "snapshot_signature": snapshot_sig,
            "continuity_score": similarity,
            "commitment_status": commitment["status"],
            "commitment_evidence_count": commitment["evidence_count"],
            "deduplicated": False,
        }

    def verify_tenant_chain(self, tenant_id: int) -> bool:
        if tenant_id < 1:
            raise RuntimeError("aporia_identity_scope_invalid")

        stmt = self.conn.prepare(
            """SELECT autobiography_entry_id, graph_commitments_json, behavior_commitments_json,
                    commitment_commitments_json, autobiography_commitments_json, ontology_commitments_json,
                    fingerprint, previous_signature, snapshot_signature
             FROM aporia_identity_snapshots WHERE tenant_id = ? ORDER BY id"""
        )
        stmt.execute([tenant_id])
        snapshots = stmt.fetch_all() or []
        prev_sig = self.ZERO_SIGNATURE
        continuity = AporiaIdentityContinuity()

        for snapshot in snapshots:
            state = {
                "graph": self._decoded_array(str(snapshot["graph_commitments_json"])),
                "behavior": self._decoded_array(str(snapshot["behavior_commitments_json"])),
                "commitment": self._decoded_array(str(snapshot["commitment_commitments_json"])),
                "autobiography": self._decoded_array(str(snapshot["autobiography_commitments_json"])),
                "ontology": self._decoded_array(str(snapshot["ontology_commitments_json"])),
            }
            fp = continuity.fingerprint(state)["fingerprint"]
            expected = hmac.new(
                self.secret.encode("utf-8"),
                f"{tenant_id}|{snapshot['autobiography_entry_id']}|{prev_sig}|{fp}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if (
                not hmac.compare_digest(fp, str(snapshot["fingerprint"]))
                or not hmac.compare_digest(prev_sig, str(snapshot["previous_signature"]))
                or not hmac.compare_digest(expected, str(snapshot["snapshot_signature"]))
            ):
                return False
            prev_sig = str(snapshot["snapshot_signature"])

        head_stmt = self.conn.prepare(
            "SELECT last_signature, snapshot_count FROM aporia_identity_heads WHERE tenant_id = ? LIMIT 1"
        )
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not snapshots:
            return head is None
        return (
            head is not None
            and int(head["snapshot_count"]) == len(snapshots)
            and hmac.compare_digest(prev_sig, str(head["last_signature"]))
        )

    def _state(self, tenant_id: int) -> dict[str, list[str]]:
        graph_stmt = self.conn.prepare("SELECT canonical_sha256 FROM aporia_events WHERE tenant_id = ? ORDER BY id")
        graph_stmt.execute([tenant_id])
        graph = [str(r["canonical_sha256"]) for r in graph_stmt.fetch_all() or []]

        beh_stmt = self.conn.prepare(
            "SELECT event_kind, COUNT(*) AS event_count FROM aporia_events WHERE tenant_id = ? GROUP BY event_kind ORDER BY event_kind"
        )
        beh_stmt.execute([tenant_id])
        behavior = []
        for row in beh_stmt.fetch_all() or []:
            msg = f"{tenant_id}|behavior|{row['event_kind']}|{row['event_count']}"
            behavior.append(hmac.new(self.secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest())

        comm_stmt = self.conn.prepare(
            "SELECT transformation_commitment FROM aporia_autobiography_entries WHERE tenant_id = ? ORDER BY id"
        )
        comm_stmt.execute([tenant_id])
        commitments = [str(r["transformation_commitment"]) for r in comm_stmt.fetch_all() or []]

        auto_stmt = self.conn.prepare(
            "SELECT entry_signature FROM aporia_autobiography_entries WHERE tenant_id = ? ORDER BY id"
        )
        auto_stmt.execute([tenant_id])
        autobiography = [str(r["entry_signature"]) for r in auto_stmt.fetch_all() or []]

        ont_stmt = self.conn.prepare(
            "SELECT version_commitment FROM aporia_ontology_versions WHERE tenant_id = ? AND status IN ('shadow','validated','active') ORDER BY version_number"
        )
        ont_stmt.execute([tenant_id])
        ontology = [str(r["version_commitment"]) for r in ont_stmt.fetch_all() or []]

        return {
            "graph": graph,
            "behavior": behavior,
            "commitment": commitments,
            "autobiography": autobiography,
            "ontology": ontology,
        }

    def _previous_state(self, tenant_id: int) -> dict[str, list[str]] | None:
        stmt = self.conn.prepare(
            """SELECT graph_commitments_json, behavior_commitments_json, commitment_commitments_json,
                    autobiography_commitments_json, ontology_commitments_json
             FROM aporia_identity_snapshots WHERE tenant_id = ? ORDER BY id DESC LIMIT 1"""
        )
        stmt.execute([tenant_id])
        snapshot = stmt.fetch()
        if not snapshot:
            return None
        return {
            "graph": self._decoded_array(str(snapshot["graph_commitments_json"])),
            "behavior": self._decoded_array(str(snapshot["behavior_commitments_json"])),
            "commitment": self._decoded_array(str(snapshot["commitment_commitments_json"])),
            "autobiography": self._decoded_array(str(snapshot["autobiography_commitments_json"])),
            "ontology": self._decoded_array(str(snapshot["ontology_commitments_json"])),
        }

    def _record_slow_commitment(
        self, tenant_id: int, autobiography_entry_id: str, causal_event_id: str, policy_hash: str, created_at: str
    ) -> dict[str, Any]:
        c_key_msg = f"{tenant_id}|shadow-policy-continuity|{policy_hash}|identity-v1"
        commitment_key = hmac.new(self.secret.encode("utf-8"), c_key_msg.encode("utf-8"), hashlib.sha256).hexdigest()
        c_id_hash = hashlib.sha256(f"{tenant_id}|{commitment_key}|slow-commitment-v1".encode("utf-8")).hexdigest()
        commitment_id = self._uuid(c_id_hash)

        insert = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_identity_commitments
               (commitment_id, tenant_id, commitment_key, commitment_kind, status, evidence_count,
                first_causal_event_id, last_causal_event_id, committed_at, schema_version, created_at, updated_at)
               VALUES (?, ?, ?, 'shadow_policy_continuity', 'proposed', 0, ?, ?, NULL, 1, ?, ?)"""
        )
        insert.execute([
            commitment_id,
            tenant_id,
            commitment_key,
            causal_event_id,
            causal_event_id,
            created_at,
            created_at,
        ])

        stmt = self.conn.prepare(
            "SELECT id, commitment_id, status, evidence_count FROM aporia_identity_commitments WHERE tenant_id = ? AND commitment_key = ? LIMIT 1"
        )
        stmt.execute([tenant_id, commitment_key])
        commitment = stmt.fetch()
        if not commitment:
            raise RuntimeError("aporia_identity_commitment_unavailable")

        existing = self.conn.prepare(
            "SELECT 1 FROM aporia_identity_commitment_evidence WHERE tenant_id = ? AND commitment_id = ? AND autobiography_entry_id = ? LIMIT 1"
        )
        existing.execute([tenant_id, commitment["id"], autobiography_entry_id])
        if existing.fetch():
            return {
                "status": str(commitment["status"]),
                "evidence_count": int(commitment["evidence_count"]),
            }

        evidence_count = int(commitment["evidence_count"]) + 1
        status = "committed" if evidence_count >= self.COMMITMENT_THRESHOLD else "proposed"
        reason_code = (
            "independent_evidence_threshold_reached"
            if status == "committed"
            else "independent_evidence_threshold_pending"
        )
        ev_hash = hashlib.sha256(f"{tenant_id}|{commitment_id}|{autobiography_entry_id}|evidence-v1".encode("utf-8")).hexdigest()
        evidence_id = self._uuid(ev_hash)

        evidence_stmt = self.conn.prepare(
            """INSERT INTO aporia_identity_commitment_evidence
             (evidence_id, tenant_id, commitment_id, autobiography_entry_id, causal_event_id,
              evidence_ordinal, resulting_status, event_name, event_version, environment, stream,
              category, component, operation_id, actor_type, action_name, lifecycle_phase, outcome,
              reason_code, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, 'aporia.identity.commitment.evidence_recorded', 1, ?, 'system',
                     'audit', 'aporia-identity-ledger', ?, 'worker', 'accumulate_slow_commitment',
                     'succeeded', 'succeeded', ?, 1, ?, ?)"""
        )
        op_id = hashlib.sha256(f"{tenant_id}|{evidence_id}|slow-commitment-evidence".encode("utf-8")).hexdigest()
        evidence_stmt.execute([
            evidence_id,
            tenant_id,
            commitment["id"],
            autobiography_entry_id,
            causal_event_id,
            evidence_count,
            status,
            self._environment(),
            op_id,
            reason_code,
            created_at,
            created_at,
        ])

        update = self.conn.prepare(
            """UPDATE aporia_identity_commitments
             SET status = ?, evidence_count = ?, last_causal_event_id = ?, committed_at = ?, updated_at = ?
             WHERE tenant_id = ? AND id = ? AND evidence_count = ?"""
        )
        committed_at_val = created_at if status == "committed" else None
        update.execute([
            status,
            evidence_count,
            causal_event_id,
            committed_at_val,
            created_at,
            tenant_id,
            commitment["id"],
            commitment["evidence_count"],
        ])
        if update.row_count != 1:
            raise RuntimeError("aporia_identity_commitment_conflict")

        return {"status": status, "evidence_count": evidence_count}

    def _ensure_head(self, tenant_id: int, created_at: str) -> None:
        stmt = self.conn.prepare(
            "INSERT OR IGNORE INTO aporia_identity_heads (tenant_id, last_signature, snapshot_count, created_at, updated_at) VALUES (?, ?, 0, ?, ?)"
        )
        stmt.execute([tenant_id, self.ZERO_SIGNATURE, created_at, created_at])

    def _decoded_array(self, json_str: str) -> list[str]:
        val = json.loads(json_str)
        if not isinstance(val, list):
            raise RuntimeError("aporia_identity_snapshot_invalid")
        return [str(v) for v in val]

    def _environment(self) -> str:
        env = os.environ.get("APP_ENV", "production").strip().lower()
        return env if env in ("production", "staging", "development", "test") else "production"

    def _uuid(self, hex_hash: str) -> str:
        hex_val = list(hex_hash.lower()[:32])
        hex_val[12] = "5"
        hex_val[16] = hex((int(hex_val[16], 16) & 0x3) | 0x8)[2:]
        h = "".join(hex_val)
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
