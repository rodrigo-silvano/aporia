"""Perspective projector and envelope generator for Aporia."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import re
from typing import Any

from aporia.infrastructure.db import Connection
from aporia.infrastructure.product_lacuna_gateway import AporiaProductLacunaGateway
from aporia.infrastructure.product_state import AporiaProductStateRepository


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_dt(dt_str: str) -> datetime:
    clean = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                pass
        raise RuntimeError("aporia_datetime_invalid")


class AporiaPerspectiveProjector:
    MAX_ATTEMPTS = 8
    PROJECTION_VERSION = 2
    LACUNA_ALGORITHM_VERSION = 2

    PERSPECTIVES = [
        "causal_continuity",
        "epistemic_provenance",
        "operational_state",
        "relationship_commitment",
        "outcome_learning",
        "identity_continuity",
    ]

    PROHIBITIONS = {
        "effect_request": True,
        "policy_mutation": True,
        "prompt_injection": True,
        "tool_injection": True,
    }

    def __init__(self, conn: Connection, product_lacuna: AporiaProductLacunaGateway | None = None):
        self.conn = conn
        self.product_lacuna = product_lacuna

    def project(self, tenant_id: int, event_row_id: int) -> dict[str, Any]:
        if tenant_id < 1 or event_row_id < 1:
            raise RuntimeError("aporia_projection_scope_invalid")

        owner_token = os.urandom(32).hex()
        owner_token_hash = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()

        self.conn.begin_transaction()
        try:
            event = self._claim(tenant_id, event_row_id, owner_token_hash)
            if str(event.get("status")) == "completed":
                self.conn.commit()
                return {"completed": True, "deduplicated": True, "observation_count": 6}

            observations = self._observations(event)
            product_state = None
            if self._table_exists("aporia_product_state_items") and self._table_exists("aporia_product_state_snapshots"):
                product_state = AporiaProductStateRepository(self.conn)

            for obs in observations:
                self._insert_observation(event, obs)
                if product_state:
                    product_state.record_perspective_observation(event, obs)
                self._checkpoint(event, str(obs["perspective"]))

            envelope_id = self._insert_envelope(event, observations)
            if product_state and self.product_lacuna:
                self._apply_product_lacuna(event, observations, product_state)

            if product_state:
                product_state.materialize_snapshot(
                    int(event["tenant_id"]),
                    str(event["event_id"]),
                    str(event["ingested_at"]),
                )

            self._enqueue_lacuna(event, envelope_id)

            stmt = self.conn.prepare(
                """UPDATE aporia_event_outbox
                 SET status = 'completed', completed_at = ?, owner_token_hash = NULL, lease_expires_at = NULL,
                     reason_code = NULL, updated_at = ?
                 WHERE tenant_id = ? AND event_id = ? AND status = 'leased'
                   AND owner_token_hash = ? AND fencing_token = ?"""
            )
            stmt.execute([
                event["ingested_at"],
                event["ingested_at"],
                tenant_id,
                event_row_id,
                owner_token_hash,
                event["fencing_token"],
            ])
            if stmt.row_count != 1:
                raise RuntimeError("aporia_projection_lease_lost")

            self.conn.commit()
            return {"completed": True, "deduplicated": False, "observation_count": len(observations)}
        except Exception as e:
            if self.conn.in_transaction():
                self.conn.roll_back()
            try:
                self._defer_failure(tenant_id, event_row_id, e)
            except Exception:
                pass
            raise

    def process_next(self, tenant_ids: list[int] | None = None) -> bool:
        t_ids = [int(x) for x in (tenant_ids or []) if int(x) > 0]
        t_ids = sorted(list(set(t_ids)))
        scope = ""
        if t_ids:
            placeholders = ",".join(["?"] * len(t_ids))
            scope = f" AND tenant_id IN ({placeholders})"

        sql = f"""SELECT tenant_id, event_id
             FROM aporia_event_outbox
             WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
             {scope}
             ORDER BY COALESCE(next_attempt_at, created_at), id
             LIMIT 1"""
        stmt = self.conn.prepare(sql)
        stmt.execute(t_ids)
        candidate = stmt.fetch()
        if not candidate:
            return False

        self.project(int(candidate["tenant_id"]), int(candidate["event_id"]))
        return True

    def envelope(self, tenant_id: int, event_row_id: int) -> dict[str, Any] | None:
        stmt = self.conn.prepare(
            """SELECT envelope_id, delivery_mode, observation_ids_json, event_count, omitted_event_count,
                    completeness, source_hlc_wall_us, source_hlc_logical, prohibitions_json, policy_hash, schema_version
             FROM aporia_shadow_envelopes WHERE tenant_id = ? AND event_id = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        row = stmt.fetch()
        if not row:
            return None

        return {
            "schema_version": int(row["schema_version"]),
            "envelope_id": str(row["envelope_id"]),
            "delivery_mode": str(row["delivery_mode"]),
            "observation_ids": json.loads(str(row["observation_ids_json"])),
            "integrity": {
                "event_count": int(row["event_count"]),
                "omitted_event_count": int(row["omitted_event_count"]),
                "completeness": str(row["completeness"]),
                "source_high_watermark": {
                    "wall_us": int(row["source_hlc_wall_us"]),
                    "logical": int(row["source_hlc_logical"]),
                },
            },
            "prohibitions": json.loads(str(row["prohibitions_json"])),
            "policy_hash": str(row["policy_hash"]),
        }

    def _claim(self, tenant_id: int, event_row_id: int, owner_token_hash: str) -> dict[str, Any]:
        stmt = self.conn.prepare(
            """SELECT event.id, event.event_id, event.tenant_id, event.session_ref, event.task_ref, event.event_kind,
                    event.source_service, event.ingested_at, event.hlc_wall_us, event.hlc_logical,
                    event.causal_depth, event.canonical_sha256, event.completeness,
                    outbox.status, outbox.fencing_token,
                    (SELECT COUNT(*) FROM aporia_event_parents edge
                     WHERE edge.tenant_id = event.tenant_id AND edge.child_event_id = event.id) AS parent_count,
                    (SELECT parent.event_kind
                     FROM aporia_event_parents edge
                     INNER JOIN aporia_events parent
                       ON parent.tenant_id = edge.tenant_id AND parent.id = edge.parent_event_id
                     WHERE edge.tenant_id = event.tenant_id AND edge.child_event_id = event.id
                       AND edge.relation_type = 'session_predecessor'
                     LIMIT 1) AS predecessor_event_kind
             FROM aporia_events event
             INNER JOIN aporia_event_outbox outbox ON outbox.tenant_id = event.tenant_id AND outbox.event_id = event.id
             WHERE event.tenant_id = ? AND event.id = ? LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        event = stmt.fetch()
        if not event:
            raise RuntimeError("aporia_projection_event_not_found")

        if str(event.get("status")) == "completed":
            return event
        if str(event.get("status")) != "pending":
            raise RuntimeError("aporia_projection_unavailable")

        fencing_token = int(event["fencing_token"]) + 1
        ingested_dt = _parse_dt(str(event["ingested_at"]))
        lease_expires_at = (ingested_dt + timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S.%f")

        upd = self.conn.prepare(
            """UPDATE aporia_event_outbox
             SET status = 'leased', fencing_token = ?, owner_token_hash = ?, attempts = attempts + 1,
                 lease_expires_at = ?, updated_at = ?
             WHERE tenant_id = ? AND event_id = ? AND status = 'pending'"""
        )
        upd.execute([
            fencing_token,
            owner_token_hash,
            lease_expires_at,
            event["ingested_at"],
            tenant_id,
            event_row_id,
        ])
        if upd.row_count != 1:
            raise RuntimeError("aporia_projection_claim_failed")

        res = dict(event)
        res["fencing_token"] = fencing_token
        res["status"] = "leased"
        return res

    def _observations(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        policy_hash = self._policy_hash()
        inp_hash_msg = f"{event['tenant_id']}|{event['event_id']}|{event['canonical_sha256']}"
        input_hash = hashlib.sha256(inp_hash_msg.encode("utf-8")).hexdigest()

        event_kind = str(event["event_kind"])
        state = self._operational_state(event_kind)
        relationship = self._relationship_state(event_kind)
        outcome = (
            {"state": "not_evaluable", "uncertainty": ["outcome_not_instrumented"]}
            if event_kind == "effect.completed"
            else {"state": "not_evaluable", "uncertainty": ["event_not_outcome"]}
        )

        definitions = {
            "causal_continuity": {
                "status": "observed",
                "value": {
                    "causal_depth": int(event["causal_depth"]),
                    "parent_count": int(event["parent_count"]),
                    "predecessor_event_kind": "root" if event["predecessor_event_kind"] is None else str(event["predecessor_event_kind"]),
                    "relation_scope": "session_predecessor_only",
                },
                "uncertainty": [],
            },
            "epistemic_provenance": {
                "status": "observed",
                "value": {
                    "evidence_count": 1,
                    "event_kind": str(event["event_kind"]),
                    "source_service": str(event["source_service"]),
                },
                "uncertainty": [],
            },
            "operational_state": {
                "status": "observed",
                "value": {"state": state},
                "uncertainty": [],
            },
            "relationship_commitment": {
                "status": "unknown" if relationship == "unknown" else "observed",
                "value": {"state": relationship},
                "uncertainty": ["no_commitment_signal"] if relationship == "unknown" else [],
            },
            "outcome_learning": {
                "status": "unknown",
                "value": {"state": outcome["state"]},
                "uncertainty": outcome["uncertainty"],
            },
            "identity_continuity": {
                "status": "observed",
                "value": {
                    "adapter_version": 1,
                    "policy_hash": policy_hash,
                    "projection_version": self.PROJECTION_VERSION,
                },
                "uncertainty": [],
            },
        }

        task_ref = str(event.get("task_ref") or "").strip()
        subject_ref = task_ref if task_ref else str(event["session_ref"])

        observations = []
        for perspective in self.PERSPECTIVES:
            definition = definitions[perspective]
            obs_hash = hashlib.sha256(
                f"{event['tenant_id']}|{event['event_id']}|{perspective}|{self.PROJECTION_VERSION}|{policy_hash}".encode("utf-8")
            ).hexdigest()
            obs_id = self._uuid(obs_hash)

            observations.append({
                "observation_id": obs_id,
                "perspective": perspective,
                "subject_ref": subject_ref,
                "epistemic_status": definition["status"],
                "confidence": None,
                "uncertainty_codes": definition["uncertainty"],
                "evidence_event_ids": [str(event["event_id"])],
                "value": definition["value"],
                "projection_version": self.PROJECTION_VERSION,
                "adapter_version": 1,
                "policy_hash": policy_hash,
                "input_event_set_hash": input_hash,
            })

        return observations

    def _insert_observation(self, event: dict[str, Any], observation: dict[str, Any]) -> None:
        stmt = self.conn.prepare(
            """INSERT INTO aporia_perspective_observations
             (observation_id, tenant_id, event_id, perspective, subject_ref, epistemic_status, confidence,
              uncertainty_codes_json, evidence_event_ids_json, value_json, projection_version, adapter_version,
              policy_hash, input_event_set_hash, produced_at, valid_until, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)"""
        )
        stmt.execute([
            observation["observation_id"],
            event["tenant_id"],
            event["id"],
            observation["perspective"],
            observation["subject_ref"],
            observation["epistemic_status"],
            observation["confidence"],
            self._json(observation["uncertainty_codes"]),
            self._json(observation["evidence_event_ids"]),
            self._json(observation["value"]),
            observation["projection_version"],
            observation["adapter_version"],
            observation["policy_hash"],
            observation["input_event_set_hash"],
            event["ingested_at"],
            event["ingested_at"],
            event["ingested_at"],
        ])

    def _insert_envelope(self, event: dict[str, Any], observations: list[dict[str, Any]]) -> str:
        env_hash = hashlib.sha256(
            f"{event['tenant_id']}|{event['event_id']}|shadow-envelope|{self.PROJECTION_VERSION}|{self._policy_hash()}".encode("utf-8")
        ).hexdigest()
        envelope_id = self._uuid(env_hash)

        stmt = self.conn.prepare(
            """INSERT INTO aporia_shadow_envelopes
             (envelope_id, tenant_id, event_id, session_ref, task_ref, delivery_mode, observation_ids_json,
              event_count, omitted_event_count, completeness, source_hlc_wall_us, source_hlc_logical,
              prohibitions_json, policy_hash, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, 'shadow', ?, 1, 0, ?, ?, ?, ?, ?, 1, ?, ?)"""
        )
        stmt.execute([
            envelope_id,
            event["tenant_id"],
            event["id"],
            event["session_ref"],
            event["task_ref"],
            self._json([obs["observation_id"] for obs in observations]),
            "complete" if event["completeness"] == "complete" else "partial",
            event["hlc_wall_us"],
            event["hlc_logical"],
            self._json(self.PROHIBITIONS),
            self._policy_hash(),
            event["ingested_at"],
            event["ingested_at"],
        ])
        return envelope_id

    def _checkpoint(self, event: dict[str, Any], perspective: str) -> None:
        stmt = self.conn.prepare(
            f"""INSERT INTO aporia_projection_checkpoints
             (tenant_id, perspective, projection_version, hlc_wall_us, hlc_logical, event_id, schema_version, created_at, updated_at)
             VALUES (?, ?, {self.PROJECTION_VERSION}, ?, ?, ?, 1, ?, ?)
             ON CONFLICT (tenant_id, perspective, projection_version) DO UPDATE SET
               hlc_wall_us = excluded.hlc_wall_us,
               hlc_logical = excluded.hlc_logical,
               event_id = excluded.event_id,
               updated_at = excluded.updated_at
             WHERE excluded.hlc_wall_us > aporia_projection_checkpoints.hlc_wall_us
                OR (excluded.hlc_wall_us = aporia_projection_checkpoints.hlc_wall_us
                    AND excluded.hlc_logical > aporia_projection_checkpoints.hlc_logical)"""
        )
        stmt.execute([
            event["tenant_id"],
            perspective,
            event["hlc_wall_us"],
            event["hlc_logical"],
            event["event_id"],
            event["ingested_at"],
            event["ingested_at"],
        ])

    def _enqueue_lacuna(self, event: dict[str, Any], envelope_id: str) -> None:
        stmt = self.conn.prepare(
            f"""INSERT INTO aporia_lacuna_outbox
             (tenant_id, source_event_id, envelope_id, algorithm_version, delivery_key, status, fencing_token, attempts, created_at, updated_at)
             VALUES (?, ?, ?, {self.LACUNA_ALGORITHM_VERSION}, ?, 'pending', 0, 0, ?, ?)"""
        )
        del_key = hashlib.sha256(f"aporia-lacuna:{event['tenant_id']}:{event['event_id']}:v{self.LACUNA_ALGORITHM_VERSION}".encode("utf-8")).hexdigest()
        stmt.execute([
            event["tenant_id"],
            event["id"],
            envelope_id,
            del_key,
            event["ingested_at"],
            event["ingested_at"],
        ])

    def _operational_state(self, event_kind: str) -> str:
        mapping = {
            "turn.started": "turn_active",
            "tool.proposed": "effect_proposed",
            "approval.requested": "approval_pending",
            "effect.started": "effect_active",
            "effect.completed": "effect_completed",
            "turn.completed": "turn_completed",
            "turn.failed": "turn_failed",
            "turn.interrupted": "turn_interrupted",
            "turn.deduplicated": "turn_deduplicated",
        }
        return mapping.get(event_kind, "unknown")

    def _relationship_state(self, event_kind: str) -> str:
        mapping = {
            "tool.proposed": "effect_proposed",
            "approval.requested": "approval_pending",
            "effect.completed": "effect_completed",
        }
        return mapping.get(event_kind, "unknown")

    def _policy_hash(self) -> str:
        return hashlib.sha256(
            self._json({
                "adapter_version": 1,
                "perspectives": self.PERSPECTIVES,
                "prohibitions": self.PROHIBITIONS,
                "projection_version": self.PROJECTION_VERSION,
            }).encode("utf-8")
        ).hexdigest()

    def _table_exists(self, table: str) -> bool:
        stmt = self.conn.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1")
        stmt.execute([table])
        return stmt.fetch() is not None

    def _apply_product_lacuna(
        self,
        event: dict[str, Any],
        observations: list[dict[str, Any]],
        product_state: AporiaProductStateRepository,
    ) -> None:
        uncertainty = []
        hypotheses = []
        for obs in observations:
            for reason in obs.get("uncertainty_codes", []):
                r = str(reason).strip()
                if r and r not in uncertainty:
                    uncertainty.append(r)
            if obs.get("epistemic_status") != "observed":
                hypotheses.append("unverified_" + str(obs.get("perspective", "unknown")))

        unique_hypotheses = []
        for h in hypotheses:
            if h not in unique_hypotheses:
                unique_hypotheses.append(h)

        derived = {
            "hypotheses": unique_hypotheses,
            "constraints": [k for k, v in self.PROHIBITIONS.items() if v],
            "uncertainty": uncertainty,
        }

        if not self.product_lacuna:
            return

        evaluated = self.product_lacuna.evaluate_safely(
            int(event["tenant_id"]),
            str(event["session_ref"]),
            str(event.get("task_ref") or event["event_id"]),
            "focus_constraints_v1",
            derived,
        )

        if not evaluated.get("available") or not isinstance(evaluated.get("result"), dict):
            return

        observed_at = str(event["ingested_at"])
        dt = _parse_dt(observed_at)
        valid_until = (dt + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S.%f")

        product_state.record({
            "tenant_id": int(event["tenant_id"]),
            "entity_id": str(event.get("task_ref") or event["session_ref"]),
            "state_type": "lacuna_state",
            "value": evaluated["result"],
            "source_ref": str(evaluated["output_commitment"]),
            "source_type": "aporia_inference",
            "observed_at": observed_at,
            "valid_from": observed_at,
            "valid_until": valid_until,
            "confidence": 0.60,
            "provenance": {
                "event_ref": str(event["event_id"]),
                "instrument": "focus_constraints_v1",
                "output_commitment": str(evaluated["output_commitment"]),
                "destruction_receipt": str(evaluated["destruction_receipt"]),
            },
            "causal_parent_ids": [str(event["event_id"])],
            "ontology_version": hashlib.sha256(b"aporia-product-ontology-v1").hexdigest(),
            "policy_version": hashlib.sha256(b"aporia-product-lacuna-v1").hexdigest(),
            "schema_version": 1,
            "created_by": "aporia-product-lacuna",
            "verified": False,
            "freshness_half_life_seconds": 1800,
        })

    def _defer_failure(self, tenant_id: int, event_row_id: int, exception: Exception) -> None:
        stmt = self.conn.prepare("SELECT attempts FROM aporia_event_outbox WHERE tenant_id = ? AND event_id = ? AND status = ? LIMIT 1")
        stmt.execute([tenant_id, event_row_id, "pending"])
        attempts = stmt.fetch_column()
        if attempts is None or attempts is False:
            return

        next_attempts = int(attempts) + 1
        status = "rejected" if next_attempts >= self.MAX_ATTEMPTS else "pending"
        delay_seconds = min(3600, 5 * (2 ** min(9, next_attempts - 1)))
        now = datetime.now(timezone.utc)
        next_attempt = (now + timedelta(seconds=delay_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f") if status == "pending" else None

        reason = ("projection_" + exception.__class__.__name__.lower())[:80]

        upd = self.conn.prepare(
            """UPDATE aporia_event_outbox
             SET status = ?, attempts = ?, next_attempt_at = ?, reason_code = ?, updated_at = ?
             WHERE tenant_id = ? AND event_id = ? AND status = ?"""
        )
        upd.execute([
            status,
            next_attempts,
            next_attempt,
            reason,
            now.strftime("%Y-%m-%d %H:%M:%S.%f"),
            tenant_id,
            event_row_id,
            "pending",
        ])

    def _uuid(self, hash_str: str) -> str:
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

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
