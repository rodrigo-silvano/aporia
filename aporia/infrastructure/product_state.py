"""
Aporia Product State Repository and Operational Metrics.
Maintains typed, authority-ranked, decaying contextual state items,
materializes point-in-time envelopes/snapshots, and computes operational observability reports.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
import math
import os
import re
from typing import Any, Mapping, Sequence
from aporia.crypto import canonical_json, sha256_hex, hmac_sha256_hex, hash_equals
from aporia.infrastructure.db import Connection


class AporiaProductStateRepository:
    AUTHORITY = {
        "model_hypothesis": 100,
        "aporia_inference": 200,
        "authorized_external": 300,
        "verified_outcome": 400,
        "backend_fact": 500,
        "synthetic_fixture": 50,
    }

    TYPES = (
        "confirmed_fact", "derived_belief", "hypothesis", "prediction", "commitment", "limitation",
        "negative_outcome", "contradiction", "uncertainty", "outcome", "causal_relation",
        "autobiographical_memory", "lacuna_state",
    )

    TTL_SECONDS = {
        "confirmed_fact": 2592000,
        "derived_belief": 604800,
        "hypothesis": 86400,
        "prediction": 604800,
        "commitment": 2592000,
        "limitation": 604800,
        "negative_outcome": 15552000,
        "contradiction": 604800,
        "uncertainty": 86400,
        "outcome": 15552000,
        "causal_relation": 7776000,
        "autobiographical_memory": 15552000,
        "lacuna_state": 3600,
    }

    POLICY_VERSION = "aporia-product-state-v1"
    SELECTION_THRESHOLD = 0.45
    MAX_SIGNALS = 24

    def __init__(self, conn: Connection | Any) -> None:
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)

    def record(self, item: Mapping[str, Any]) -> dict[str, Any]:
        item = self._validated(item)
        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare(
                "SELECT state_id, value_commitment, confidence, authority_rank, revision "
                "FROM aporia_product_state_items "
                "WHERE tenant_id = ? AND entity_id = ? AND state_type = ? AND status = ? "
                "ORDER BY revision DESC LIMIT 1"
            )
            stmt.execute([item["tenant_id"], item["entity_id"], item["state_type"], "active"])
            existing = stmt.fetch()

            revision = int(existing["revision"]) + 1 if existing else 1
            status = "active"
            reason = "state_accepted"
            supersedes = str(existing["state_id"]) if existing else None

            if (
                existing
                and not hash_equals(str(existing["value_commitment"]), item["value_commitment"])
                and int(existing["authority_rank"]) > item["authority_rank"]
            ):
                status = "rejected"
                reason = "lower_authority_conflict"
                supersedes = None

            if (
                existing
                and item["state_type"] in ("commitment", "autobiographical_memory")
                and item["confidence"] > float(existing["confidence"]) + 0.10
            ):
                item["confidence"] = round(float(existing["confidence"]) + 0.10, 5)
                reason = "slow_update_limit_applied"

            state_id = self._uuid(sha256_hex(f"{item['tenant_id']}|{item['entity_id']}|{item['state_type']}|{revision}|{item['value_commitment']}|{item['source_ref']}"))

            insert = self.conn.prepare("""
                INSERT INTO aporia_product_state_items
                    (state_id, tenant_id, entity_id, state_type, value_json, value_commitment,
                     source_ref, source_type, observed_at, valid_from, valid_until, confidence,
                     provenance_json, causal_parent_ids_json, ontology_version, policy_version,
                     schema_version, created_by, verified, authority_rank, freshness_half_life_seconds,
                     revision, status, reason_code, supersedes_state_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)
            insert.execute([
                state_id,
                item["tenant_id"],
                item["entity_id"],
                item["state_type"],
                json.dumps(item["value"], separators=(",", ":"), ensure_ascii=False),
                item["value_commitment"],
                item["source_ref"],
                item["source_type"],
                item["observed_at"],
                item["valid_from"],
                item["valid_until"],
                item["confidence"],
                json.dumps(item["provenance"], separators=(",", ":"), ensure_ascii=False),
                json.dumps(item["causal_parent_ids"], separators=(",", ":"), ensure_ascii=False),
                item["ontology_version"],
                item["policy_version"],
                item["schema_version"],
                item["created_by"],
                1 if item["verified"] else 0,
                item["authority_rank"],
                item["freshness_half_life_seconds"],
                revision,
                status,
                reason,
                supersedes,
            ])

            if status == "active" and existing:
                update = self.conn.prepare(
                    "UPDATE aporia_product_state_items SET status = 'superseded', updated_at = CURRENT_TIMESTAMP "
                    "WHERE tenant_id = ? AND state_id = ? AND status = 'active'"
                )
                update.execute([item["tenant_id"], existing["state_id"]])

            if started:
                self.conn.commit()

            return {
                "state_id": state_id,
                "revision": revision,
                "accepted": status == "active",
                "reason_code": reason,
            }
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def record_perspective_observation(self, event: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
        perspective = str(observation.get("perspective", ""))
        mapping = {
            "causal_continuity": "causal_relation",
            "epistemic_provenance": "confirmed_fact",
            "operational_state": "confirmed_fact",
            "relationship_commitment": "commitment",
            "outcome_learning": "uncertainty",
            "identity_continuity": "autobiographical_memory",
        }
        if perspective not in mapping:
            raise RuntimeError("aporia_product_state_perspective_invalid")

        observed_at = str(event.get("ingested_at", ""))
        observed = (observation.get("epistemic_status") == "observed")
        state_type = mapping[perspective] if observed else "uncertainty"
        ttl = self.TTL_SECONDS[state_type]

        dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        valid_until_dt = dt + timedelta(seconds=ttl)
        valid_until = valid_until_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        return self.record({
            "tenant_id": int(event.get("tenant_id", 0)),
            "entity_id": str(observation.get("subject_ref", "")),
            "state_type": state_type,
            "value": observation.get("value") if isinstance(observation.get("value"), dict) else {},
            "source_ref": str(observation.get("observation_id", "")),
            "source_type": "backend_fact" if observed else "aporia_inference",
            "observed_at": observed_at,
            "valid_from": observed_at,
            "valid_until": valid_until,
            "confidence": 0.95 if observed else 0.25,
            "provenance": {
                "observation_id": str(observation.get("observation_id", "")),
                "event_ref": str(event.get("event_id", "")),
                "perspective": perspective,
            },
            "causal_parent_ids": observation.get("evidence_event_ids") if isinstance(observation.get("evidence_event_ids"), list) else [],
            "ontology_version": sha256_hex("aporia-product-ontology-v1"),
            "policy_version": sha256_hex(self.POLICY_VERSION),
            "schema_version": 1,
            "created_by": "aporia-perspective-projector",
            "verified": observed,
            "freshness_half_life_seconds": max(1, ttl // 2),
        })

    def recordPerspectiveObservation(self, event: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
        return self.record_perspective_observation(event, observation)

    def materialize_snapshot(self, tenant_id: int, source_event_ref: str, created_at: str | datetime) -> dict[str, Any]:
        if tenant_id < 1 or not str(source_event_ref).strip() or not created_at:
            raise RuntimeError("aporia_product_snapshot_invalid")

        stmt = self.conn.prepare(
            "SELECT * FROM aporia_product_state_items "
            "WHERE tenant_id = ? AND status IN ('active','rejected') "
            "ORDER BY revision DESC, id DESC LIMIT 200"
        )
        stmt.execute([tenant_id])
        rows = stmt.fetchAll()

        if isinstance(created_at, datetime):
            now_dt = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
            created_at_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        else:
            created_at_str = str(created_at)
            now_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)

        candidates = []
        for row in rows:
            obs_dt = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=timezone.utc)
            valid_until_dt = datetime.fromisoformat(str(row["valid_until"]).replace("Z", "+00:00"))
            if valid_until_dt.tzinfo is None:
                valid_until_dt = valid_until_dt.replace(tzinfo=timezone.utc)

            age = max(0.0, (now_dt - obs_dt).total_seconds())
            half_life = max(1, int(row["freshness_half_life_seconds"]))
            freshness = max(0.0, min(1.0, 2.0 ** (-age / half_life)))
            score = max(0.0, min(1.0,
                0.30 * freshness
                + 0.20 * (1.0 if int(row["verified"]) == 1 else 0.0)
                + 0.20 * float(row["confidence"])
                + 0.15 * min(1.0, int(row["authority_rank"]) / 500)
                + 0.10
                - (0.20 if valid_until_dt <= now_dt else 0.0)
                - (0.15 if str(row["state_type"]) == "contradiction" else 0.0)
            ))
            if str(row["status"]) == "rejected":
                selection = "rejected"
            elif valid_until_dt <= now_dt or score < self.SELECTION_THRESHOLD:
                selection = "excluded"
            else:
                selection = "selected"

            candidates.append({
                "state_id": str(row["state_id"]),
                "entity_id": str(row["entity_id"]),
                "state_type": str(row["state_type"]),
                "value": json.loads(str(row["value_json"])),
                "confidence": float(row["confidence"]),
                "freshness": round(freshness, 5),
                "relevance": round(score, 5),
                "verified": bool(row["verified"]),
                "provenance_ref": str(row["source_ref"]),
                "selection": selection,
                "reason_code": "above_relevance_threshold" if selection == "selected" else str(row["reason_code"]),
            })

        candidates.sort(key=lambda x: x["relevance"], reverse=True)
        selected = [c for c in candidates if c["selection"] == "selected"][:self.MAX_SIGNALS]

        groups = {
            "confirmed_facts": ["confirmed_fact"],
            "relevant_commitments": ["commitment"],
            "verified_negative_outcomes": ["negative_outcome"],
            "active_constraints": ["limitation", "lacuna_state"],
            "unresolved_conflicts": ["contradiction"],
            "uncertainty": ["uncertainty", "hypothesis"],
        }
        envelope: dict[str, Any] = {}
        for name, types in groups.items():
            envelope[name] = [s for s in selected if s["state_type"] in types]

        envelope["recommended_evidence"] = [
            {"state_id": s["state_id"], "reason_code": "resolve_uncertainty"}
            for s in envelope["uncertainty"][:5]
        ]
        seen_prov = set()
        prov_refs = []
        for s in selected:
            p = s["provenance_ref"]
            if p not in seen_prov:
                seen_prov.add(p)
                prov_refs.append(p)
        envelope["provenance_refs"] = prov_refs
        envelope["selection_audit"] = candidates
        envelope["limitations"] = ["non_authoritative", "no_effect_permission", "no_chain_of_thought"]
        envelope["schema_version"] = 1

        revision = max([int(r["revision"]) for r in rows] or [0])
        policy_version = sha256_hex(self.POLICY_VERSION)
        snapshot_id = self._uuid(sha256_hex(f"{tenant_id}|{source_event_ref}|{revision}|{json.dumps(envelope, separators=(',', ':'), ensure_ascii=False)}"))
        valid_until_dt = now_dt + timedelta(minutes=15)
        valid_until = valid_until_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        excluded_count = sum(1 for c in candidates if c["selection"] == "excluded")
        rejected_count = sum(1 for c in candidates if c["selection"] == "rejected")

        insert_stmt = self.conn.prepare(
            "INSERT OR IGNORE INTO aporia_product_state_snapshots "
            "(snapshot_id, tenant_id, source_event_ref, state_revision, envelope_json, "
            " candidate_count, selected_count, excluded_count, rejected_count, "
            " policy_version, schema_version, valid_until, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
        )
        insert_stmt.execute([
            snapshot_id,
            tenant_id,
            source_event_ref,
            revision,
            json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
            len(candidates),
            len(selected),
            excluded_count,
            rejected_count,
            policy_version,
            valid_until,
            created_at_str,
        ])

        return {
            "snapshot_id": snapshot_id,
            "state_revision": revision,
            "envelope": envelope,
            "valid_until": valid_until,
        }

    def materializeSnapshot(self, tenant_id: int, source_event_ref: str, created_at: str | datetime) -> dict[str, Any]:
        return self.materialize_snapshot(tenant_id, source_event_ref, created_at)

    def latest_snapshot(self, tenant_id: int, now: str | datetime | None = None) -> dict[str, Any] | None:
        if tenant_id < 1:
            raise RuntimeError("aporia_product_snapshot_invalid")
        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif isinstance(now, datetime):
            now_dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        else:
            now_dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)

        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        stmt = self.conn.prepare(
            "SELECT snapshot_id, state_revision, envelope_json, policy_version, valid_until "
            "FROM aporia_product_state_snapshots "
            "WHERE tenant_id = ? AND valid_until > ? ORDER BY state_revision DESC, id DESC LIMIT 1"
        )
        stmt.execute([tenant_id, now_str])
        row = stmt.fetch()
        if not row:
            return None
        return {
            "snapshot_id": str(row["snapshot_id"]),
            "state_revision": int(row["state_revision"]),
            "policy_version": str(row["policy_version"]),
            "valid_until": str(row["valid_until"]),
            "envelope": json.loads(str(row["envelope_json"])),
        }

    def latestSnapshot(self, tenant_id: int, now: str | datetime | None = None) -> dict[str, Any] | None:
        return self.latest_snapshot(tenant_id, now)

    def _validated(self, item: Mapping[str, Any]) -> dict[str, Any]:
        required = [
            "tenant_id", "entity_id", "state_type", "value", "source_ref", "source_type", "observed_at",
            "valid_from", "valid_until", "confidence", "provenance", "causal_parent_ids", "ontology_version",
            "policy_version", "schema_version", "created_by", "verified", "freshness_half_life_seconds",
        ]
        for key in required:
            if key not in item:
                raise RuntimeError("aporia_product_state_invalid")

        tenant_id = int(item["tenant_id"])
        entity_id = str(item["entity_id"]).strip()
        state_type = str(item["state_type"])
        source_type = str(item["source_type"])
        value = item["value"]
        provenance = item["provenance"]
        causal_parent_ids = item["causal_parent_ids"]
        verified = item["verified"]
        confidence = float(item["confidence"])
        ontology_version = str(item["ontology_version"])
        policy_version = str(item["policy_version"])
        schema_version = int(item["schema_version"])
        freshness_half_life_seconds = int(item["freshness_half_life_seconds"])

        if (
            tenant_id < 1
            or entity_id == ""
            or state_type not in self.TYPES
            or source_type not in self.AUTHORITY
            or not isinstance(value, dict)
            or not isinstance(provenance, dict)
            or not isinstance(causal_parent_ids, list)
            or not isinstance(verified, bool)
            or confidence < 0.0
            or confidence > 1.0
            or not re.match(r"^[0-9a-f]{64}$", ontology_version)
            or not re.match(r"^[0-9a-f]{64}$", policy_version)
            or schema_version < 1
            or freshness_half_life_seconds < 1
        ):
            raise RuntimeError("aporia_product_state_invalid")

        if (state_type == "confirmed_fact" and source_type != "backend_fact") or (
            state_type in ("outcome", "negative_outcome")
            and source_type not in ("verified_outcome", "backend_fact")
        ):
            raise RuntimeError("aporia_product_state_authority_invalid")

        val_json = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        val_comm = sha256_hex(val_json)

        res = dict(item)
        res["tenant_id"] = tenant_id
        res["entity_id"] = entity_id[:191]
        res["source_ref"] = str(item["source_ref"]).strip()[:191]
        res["created_by"] = str(item["created_by"]).strip()[:80]
        res["confidence"] = round(confidence, 5)
        res["authority_rank"] = self.AUTHORITY[source_type]
        res["value_commitment"] = val_comm
        return res

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


class AporiaProductOperationalMetrics:
    REQUIRED_TABLES = (
        "aporia_product_state_items",
        "aporia_product_state_snapshots",
        "aporia_product_context_exposures",
        "aporia_product_runtime_outcomes",
        "aporia_product_lacuna_one_shots",
        "aporia_effect_contracts",
        "aporia_effect_outcomes",
        "ai_provider_response_metadata",
    )

    def __init__(self, conn: Connection | Any) -> None:
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)

    def report(self, tenant_id: int | None = None, hours: int = 24) -> dict[str, Any]:
        if (tenant_id is not None and tenant_id < 1) or hours < 1 or hours > 24 * 30:
            raise ValueError("aporia_product_metrics_scope_invalid")

        missing = [t for t in self.REQUIRED_TABLES if not self._table_exists(t)]
        if missing:
            return {
                "available": False,
                "status": "unavailable",
                "tenant_id": tenant_id,
                "window_hours": hours,
                "missing_tables": missing,
                "alerts": [{"severity": "critical", "code": "aporia_product_observability_unavailable"}],
            }

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S.%f")
        params = [cutoff] if tenant_id is None else [cutoff, tenant_id]
        scope = "" if tenant_id is None else " AND tenant_id = ?"

        # 1. Exposures
        stmt_exp = self.conn.prepare(
            f"SELECT mode AS metric_key, COUNT(*) AS metric_value "
            f"FROM aporia_product_context_exposures WHERE created_at >= ?{scope} GROUP BY mode"
        )
        stmt_exp.execute(params)
        exposures = {str(r["metric_key"]): int(r["metric_value"]) for r in stmt_exp.fetchAll()}

        # 2. Outcomes
        scope_alias = "" if tenant_id is None else " AND outcome.tenant_id = ?"
        stmt_out = self.conn.prepare(
            f"SELECT outcome.helpful, outcome.latency_ms, outcome.cost_units, outcome.critical_regression, "
            f"       outcome.evaluability "
            f"FROM aporia_product_runtime_outcomes outcome "
            f"WHERE outcome.created_at >= ?{scope_alias}"
        )
        stmt_out.execute(params)
        outcome_rows = stmt_out.fetchAll()

        # 3. Snapshot aggregations
        stmt_snap = self.conn.prepare(
            f"SELECT COUNT(*) AS snapshots, COALESCE(SUM(candidate_count), 0) AS candidates, "
            f"       COALESCE(SUM(selected_count), 0) AS selected, "
            f"       COALESCE(SUM(excluded_count), 0) AS excluded, "
            f"       COALESCE(SUM(rejected_count), 0) AS rejected "
            f"FROM aporia_product_state_snapshots WHERE created_at >= ?{scope}"
        )
        stmt_snap.execute(params)
        snap_agg = stmt_snap.fetch() or {}

        # 4. State items
        stmt_st = self.conn.prepare(
            f"SELECT (state_type || ':' || status) AS metric_key, COUNT(*) AS metric_value "
            f"FROM aporia_product_state_items WHERE created_at >= ?{scope} GROUP BY state_type, status"
        )
        stmt_st.execute(params)
        state_types = {str(r["metric_key"]): int(r["metric_value"]) for r in stmt_st.fetchAll()}

        # 5. HIRT decisions
        stmt_hirt = self.conn.prepare(
            f"SELECT (mode || ':' || decision) AS metric_key, COUNT(*) AS metric_value "
            f"FROM aporia_effect_contracts WHERE created_at >= ?{scope} GROUP BY mode, decision"
        )
        stmt_hirt.execute(params)
        hirt_decisions = {str(r["metric_key"]): int(r["metric_value"]) for r in stmt_hirt.fetchAll()}

        # 6. HIRT outcomes
        stmt_ho = self.conn.prepare(
            f"SELECT state AS metric_key, COUNT(*) AS metric_value "
            f"FROM aporia_effect_outcomes WHERE created_at >= ?{scope} GROUP BY state"
        )
        stmt_ho.execute(params)
        hirt_outcomes = {str(r["metric_key"]): int(r["metric_value"]) for r in stmt_ho.fetchAll()}

        # 7. Lacuna one-shots
        stmt_lac = self.conn.prepare(
            f"SELECT status AS metric_key, COUNT(*) AS metric_value "
            f"FROM aporia_product_lacuna_one_shots WHERE created_at >= ?{scope} GROUP BY status"
        )
        stmt_lac.execute(params)
        lacuna = {str(r["metric_key"]): int(r["metric_value"]) for r in stmt_lac.fetchAll()}

        # 8. Provider response metadata
        provider_scope = "global" if tenant_id is None else "unavailable_per_tenant"
        if tenant_id is None:
            stmt_prov = self.conn.prepare("""
                SELECT COUNT(*) AS attempts,
                       COALESCE(SUM(CASE WHEN status <> 'completed' THEN 1 ELSE 0 END), 0) AS non_completed,
                       COALESCE(SUM(CASE WHEN incomplete_reason IS NOT NULL THEN 1 ELSE 0 END), 0) AS incomplete,
                       COALESCE(SUM(CASE WHEN error_code IN ('schema_error','empty_output') THEN 1 ELSE 0 END), 0) AS parse_failures,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(latency_ms), 0) AS latency_ms
                FROM ai_provider_response_metadata WHERE created_at >= ?
            """)
            stmt_prov.execute([cutoff])
            provider = stmt_prov.fetch() or {}
        else:
            provider = {
                "attempts": 0, "non_completed": 0, "incomplete": 0, "parse_failures": 0, "input_tokens": 0,
                "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0, "latency_ms": 0,
            }

        # 9. Integrity checks
        scope_exp_alias = "" if tenant_id is None else " AND exposure.tenant_id = ?"
        stmt_mismatch1 = self.conn.prepare(f"""
            SELECT COUNT(*) FROM aporia_product_context_exposures exposure
            WHERE exposure.created_at >= ?{scope_exp_alias}
              AND NOT EXISTS (
                  SELECT 1 FROM aporia_product_state_snapshots snapshot
                  WHERE snapshot.tenant_id = exposure.tenant_id AND snapshot.snapshot_id = exposure.snapshot_id
              )
        """)
        stmt_mismatch1.execute(params)
        mismatch1 = int(stmt_mismatch1.fetchColumn() or 0)

        scope_out_alias = "" if tenant_id is None else " AND outcome.tenant_id = ?"
        stmt_mismatch2 = self.conn.prepare(f"""
            SELECT COUNT(*) FROM aporia_product_runtime_outcomes outcome
            WHERE outcome.created_at >= ?{scope_out_alias}
              AND NOT EXISTS (
                  SELECT 1 FROM aporia_product_context_exposures exposure
                  WHERE exposure.tenant_id = outcome.tenant_id AND exposure.exposure_id = outcome.exposure_id
              )
        """)
        stmt_mismatch2.execute(params)
        mismatch2 = int(stmt_mismatch2.fetchColumn() or 0)

        scope_ho_alias = "" if tenant_id is None else " AND effect_outcome.tenant_id = ?"
        stmt_mismatch3 = self.conn.prepare(f"""
            SELECT COUNT(*) FROM aporia_effect_outcomes effect_outcome
            WHERE effect_outcome.created_at >= ?{scope_ho_alias}
              AND NOT EXISTS (
                  SELECT 1 FROM aporia_effect_contracts contract
                  WHERE contract.tenant_id = effect_outcome.tenant_id
                    AND contract.operation_id = effect_outcome.operation_id
              )
        """)
        stmt_mismatch3.execute(params)
        mismatch3 = int(stmt_mismatch3.fetchColumn() or 0)

        integrity = {
            "exposure_snapshot_mismatch": mismatch1,
            "outcome_exposure_mismatch": mismatch2,
            "hirt_outcome_contract_mismatch": mismatch3,
        }

        # 10. Unsafe effects
        stmt_unsafe = self.conn.prepare(f"""
            SELECT
                COALESCE(SUM(CASE WHEN mode = 'guarded_reversible' AND allowed = 1
                    AND (decision <> 'safe_prefix_allowed' OR reversible <> 1 OR external_effect <> 0
                         OR idempotent <> 1 OR approval_required <> 1 OR guarded_eligible <> 1) THEN 1 ELSE 0 END), 0) AS unsafe_guarded,
                COALESCE(SUM(CASE WHEN mode = 'shadow' AND allowed = 1 AND EXISTS (
                    SELECT 1 FROM aporia_effect_outcomes outcome
                    WHERE outcome.tenant_id = aporia_effect_contracts.tenant_id
                      AND outcome.operation_id = aporia_effect_contracts.operation_id
                ) THEN 1 ELSE 0 END), 0) AS shadow_effects
            FROM aporia_effect_contracts WHERE created_at >= ?{scope}
        """)
        stmt_unsafe.execute(params)
        unsafe = stmt_unsafe.fetch() or {}

        latencies = [int(r["latency_ms"]) for r in outcome_rows]
        evaluated = [r for r in outcome_rows if r.get("evaluability") == "observed"]
        helpful = sum(1 for r in evaluated if int(r.get("helpful") or 0) == 1)
        critical_regressions = sum(int(r.get("critical_regression") or 0) for r in outcome_rows)
        cross_tenant = sum(integrity.values())

        alerts: list[dict[str, Any]] = []
        if cross_tenant > 0:
            alerts.append({"severity": "critical", "code": "aporia_cross_tenant_integrity_failure", "count": cross_tenant})
        if int(unsafe.get("unsafe_guarded", 0)) > 0 or int(unsafe.get("shadow_effects", 0)) > 0:
            count = int(unsafe.get("unsafe_guarded", 0)) + int(unsafe.get("shadow_effects", 0))
            alerts.append({"severity": "critical", "code": "aporia_unsafe_effect_observed", "count": count})
        if critical_regressions > 0:
            alerts.append({"severity": "critical", "code": "aporia_critical_regression_observed", "count": critical_regressions})
        if provider_scope == "global" and int(provider.get("parse_failures", 0)) > 0:
            alerts.append({"severity": "warning", "code": "ai_structured_output_failure", "count": int(provider.get("parse_failures", 0))})

        attempts = max(0, int(provider.get("attempts", 0)))
        if provider_scope == "global" and attempts >= 20 and (int(provider.get("incomplete", 0)) / attempts) > 0.02:
            alerts.append({"severity": "warning", "code": "ai_incomplete_rate_elevated", "ratio": round(int(provider.get("incomplete", 0)) / attempts, 5)})

        status = "critical" if any(a["severity"] == "critical" for a in alerts) else ("warning" if any(a["severity"] == "warning" for a in alerts) else "healthy")

        p95_lat = self._percentile(latencies, 0.95)
        total_cost = sum(float(r.get("cost_units", 0.0)) for r in outcome_rows)

        provider_cached_ratio = (
            round(int(provider["cached_tokens"]) / int(provider["input_tokens"]), 5)
            if provider_scope == "global" and int(provider.get("input_tokens", 0)) > 0
            else None
        )

        return {
            "available": True,
            "status": status,
            "tenant_id": tenant_id,
            "window_hours": hours,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "quality": {
                "evaluated_outcomes": len(evaluated),
                "helpful_outcomes": helpful,
                "helpful_rate": round(helpful / len(evaluated), 5) if evaluated else None,
                "critical_regressions": critical_regressions,
            },
            "latency_cost": {
                "outcome_latency_p95_ms": p95_lat,
                "outcome_cost_units": round(total_cost, 4),
                "provider_cached_ratio": provider_cached_ratio,
                "provider_scope": provider_scope,
                "provider": {k: int(v) for k, v in provider.items()},
            },
            "context": {
                "exposures": exposures,
                "snapshots": {k: int(v) for k, v in snap_agg.items()},
                "state_types": state_types,
            },
            "hirt": {
                "decisions": hirt_decisions,
                "outcomes": hirt_outcomes,
                "unsafe": {k: int(v) for k, v in unsafe.items()},
            },
            "lacuna": lacuna,
            "integrity": integrity,
            "alerts": alerts,
        }

    def _table_exists(self, table: str) -> bool:
        stmt = self.conn.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1")
        stmt.execute([table])
        return stmt.fetchColumn() is not False

    @staticmethod
    def _percentile(values: Sequence[int], p: float) -> int | None:
        if not values:
            return None
        sorted_vals = sorted(values)
        idx = int(math.ceil(p * len(sorted_vals))) - 1
        return sorted_vals[max(0, min(len(sorted_vals) - 1, idx))]
