"""Autobiography ledger, evidence instruments, and negative autobiography evaluation for Aporia."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from typing import Any

from aporia.infrastructure.causal_semantics import AporiaCausalEventSemanticsV2
from aporia.infrastructure.db import Connection


class AporiaAutobiographyLedger:
    ZERO_SIGNATURE = "0000000000000000000000000000000000000000000000000000000000000000"
    MECHANISM_ONLY_REASON_CODES = [
        "negative_autobiography_not_instrumented",
        "lost_distinctions_not_observed",
        "excluded_futures_not_observed",
        "structural_shadow_only",
        "content_not_persisted",
    ]

    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_autobiography_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def record_mechanism_only(self, source: dict[str, Any], run_row_id: int, run_id: str, input_commitment: str) -> dict[str, Any]:
        return self.record(source, run_row_id, run_id, input_commitment, {
            "evaluability": "not_evaluable",
            "lost_distinction_hashes": [],
            "excluded_future_hashes": [],
            "irrecoverability": None,
            "causal_efficacy": None,
            "confidence": None,
            "reason_codes": list(self.MECHANISM_ONLY_REASON_CODES),
        })

    def record(
        self,
        source: dict[str, Any],
        run_row_id: int,
        run_id: str,
        input_commitment: str,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_autobiography_transaction_required")

        tenant_id = int(source.get("tenant_id", 0))
        event_id = str(source.get("event_id", ""))
        episode_ref = str(source.get("task_ref") or source.get("session_ref") or "")
        policy_hash = str(source.get("policy_hash", ""))
        created_at = str(source.get("ingested_at", ""))

        if tenant_id < 1 or run_row_id < 1 or not event_id or not episode_ref or not policy_hash or not created_at:
            raise RuntimeError("aporia_autobiography_scope_invalid")

        norm_eval = self._evaluation(evaluation)
        entry_kind = "negative_observation" if norm_eval["evaluability"] == "observed" else "mechanism_only"
        action_name = "record_negative_observation" if entry_kind == "negative_observation" else "record_mechanism_only"
        reason_code = (
            "negative_autobiography_observed"
            if entry_kind == "negative_observation"
            else "negative_autobiography_not_instrumented"
        )

        self._ensure_head(tenant_id, created_at)
        head_stmt = self.conn.prepare(
            "SELECT last_signature, entry_count FROM aporia_autobiography_heads WHERE tenant_id = ? LIMIT 1"
        )
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not head:
            raise RuntimeError("aporia_autobiography_head_unavailable")

        existing = self.conn.prepare(
            "SELECT entry_id, entry_signature FROM aporia_autobiography_entries WHERE tenant_id = ? AND run_id = ? LIMIT 1"
        )
        existing.execute([tenant_id, run_row_id])
        entry = existing.fetch()
        if entry:
            return {
                "entry_id": str(entry["entry_id"]),
                "entry_signature": str(entry["entry_signature"]),
                "deduplicated": True,
            }

        prev_sig = str(head["last_signature"])
        entry_hash = hashlib.sha256(f"{tenant_id}|{event_id}|{run_id}|autobiography-v1".encode("utf-8")).hexdigest()
        entry_id = self._uuid(entry_hash)

        trans_hash = hashlib.sha256(f"{tenant_id}|{event_id}|{run_id}|{input_commitment}|{policy_hash}".encode("utf-8")).hexdigest()

        payload = self._payload(
            entry_id,
            tenant_id,
            run_id,
            event_id,
            episode_ref,
            input_commitment,
            trans_hash,
            policy_hash,
            entry_kind,
            norm_eval,
        )
        payload_hash = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
        signature = self._signature(tenant_id, event_id, prev_sig, payload_hash)
        op_id = hashlib.sha256(f"{tenant_id}|{entry_id}|record-autobiography-v2".encode("utf-8")).hexdigest()

        stmt = self.conn.prepare(
            """INSERT INTO aporia_autobiography_entries
             (entry_id, tenant_id, run_id, causal_event_id, episode_ref, entry_kind, evaluability,
              ontology_before_commitment, ontology_after_commitment, transformation_commitment,
              lost_distinction_hashes_json, excluded_future_hashes_json, required_primitive_ids_json,
              commitment_refs_json, outcome_refs_json, irrecoverability, causal_efficacy,
              autobiographic_time, confidence, reason_codes_json, policy_hash, payload_hash,
              previous_signature, entry_signature, event_name, event_version, environment, stream,
              category, component, operation_id, actor_type, action_name, lifecycle_phase, outcome,
              reason_code, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, '[]',
                     '[]', '[]', ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                     'aporia.autobiography.entry.recorded', 1, ?, 'system', 'audit',
                     'aporia-autobiography-ledger', ?, 'worker', ?, 'succeeded',
                     'succeeded', ?, 2, ?, ?)"""
        )
        stmt.execute([
            entry_id,
            tenant_id,
            run_row_id,
            event_id,
            episode_ref,
            entry_kind,
            norm_eval["evaluability"],
            input_commitment,
            trans_hash,
            self._json(norm_eval["lost_distinction_hashes"]),
            self._json(norm_eval["excluded_future_hashes"]),
            norm_eval["irrecoverability"],
            norm_eval["causal_efficacy"],
            norm_eval["confidence"],
            self._json(norm_eval["reason_codes"]),
            policy_hash,
            payload_hash,
            prev_sig,
            signature,
            self._environment(),
            op_id,
            action_name,
            reason_code,
            created_at,
            created_at,
        ])

        update = self.conn.prepare(
            """UPDATE aporia_autobiography_heads SET last_signature = ?, entry_count = ?, updated_at = ?
             WHERE tenant_id = ? AND last_signature = ? AND entry_count = ?"""
        )
        update.execute([
            signature,
            int(head["entry_count"]) + 1,
            created_at,
            tenant_id,
            prev_sig,
            int(head["entry_count"]),
        ])
        if update.row_count != 1:
            raise RuntimeError("aporia_autobiography_head_conflict")

        return {
            "entry_id": entry_id,
            "entry_signature": signature,
            "deduplicated": False,
        }

    def verify_tenant_chain(self, tenant_id: int) -> bool:
        if tenant_id < 1:
            raise RuntimeError("aporia_autobiography_scope_invalid")

        stmt = self.conn.prepare(
            """SELECT entry_id, tenant_id, run_id, causal_event_id, episode_ref, entry_kind, evaluability,
                    ontology_before_commitment, ontology_after_commitment, transformation_commitment,
                    lost_distinction_hashes_json, excluded_future_hashes_json, required_primitive_ids_json,
                    commitment_refs_json, outcome_refs_json, irrecoverability, causal_efficacy,
                    autobiographic_time, confidence, reason_codes_json, policy_hash, payload_hash,
                    previous_signature, entry_signature, schema_version
             FROM aporia_autobiography_entries WHERE tenant_id = ? ORDER BY id"""
        )
        stmt.execute([tenant_id])
        entries = stmt.fetch_all() or []
        prev_sig = self.ZERO_SIGNATURE

        for entry in entries:
            payload = self._payload_from_row(entry)
            payload_hash = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
            expected_sig = self._signature(tenant_id, str(entry["causal_event_id"]), prev_sig, payload_hash)

            if (
                not hmac.compare_digest(payload_hash, str(entry["payload_hash"]))
                or not hmac.compare_digest(prev_sig, str(entry["previous_signature"]))
                or not hmac.compare_digest(expected_sig, str(entry["entry_signature"]))
            ):
                return False
            prev_sig = str(entry["entry_signature"])

        head_stmt = self.conn.prepare("SELECT last_signature, entry_count FROM aporia_autobiography_heads WHERE tenant_id = ? LIMIT 1")
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not entries:
            return head is None
        return (
            head is not None
            and int(head["entry_count"]) == len(entries)
            and hmac.compare_digest(prev_sig, str(head["last_signature"]))
        )

    def _ensure_head(self, tenant_id: int, created_at: str) -> None:
        stmt = self.conn.prepare(
            "INSERT OR IGNORE INTO aporia_autobiography_heads (tenant_id, last_signature, entry_count, created_at, updated_at) VALUES (?, ?, 0, ?, ?)"
        )
        stmt.execute([tenant_id, self.ZERO_SIGNATURE, created_at, created_at])

    def _payload(
        self,
        entry_id: str,
        tenant_id: int,
        run_id: str,
        event_id: str,
        episode_ref: str,
        ontology_before: str,
        trans_commitment: str,
        policy_hash: str,
        entry_kind: str,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "entry_id": entry_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "causal_event_id": event_id,
            "episode_ref": episode_ref,
            "entry_kind": entry_kind,
            "evaluability": evaluation["evaluability"],
            "ontology_before_commitment": ontology_before,
            "ontology_after_commitment": None,
            "transformation_commitment": trans_commitment,
            "lost_distinction_hashes": evaluation["lost_distinction_hashes"],
            "excluded_future_hashes": evaluation["excluded_future_hashes"],
            "required_primitive_ids": [],
            "commitment_refs": [],
            "outcome_refs": [],
            "irrecoverability": evaluation["irrecoverability"],
            "causal_efficacy": evaluation["causal_efficacy"],
            "autobiographic_time": None,
            "confidence": evaluation["confidence"],
            "reason_codes": evaluation["reason_codes"],
            "policy_hash": policy_hash,
            "schema_version": 2,
        }

    def _payload_from_row(self, entry: dict[str, Any]) -> dict[str, Any]:
        irrec = entry["irrecoverability"]
        causal = entry["causal_efficacy"]
        auto_time = entry["autobiographic_time"]
        conf = entry["confidence"]

        return {
            "entry_id": str(entry["entry_id"]),
            "tenant_id": int(entry["tenant_id"]),
            "run_id": self._public_run_id(int(entry["tenant_id"]), int(entry["run_id"])),
            "causal_event_id": str(entry["causal_event_id"]),
            "episode_ref": str(entry["episode_ref"]),
            "entry_kind": str(entry["entry_kind"]),
            "evaluability": str(entry["evaluability"]),
            "ontology_before_commitment": str(entry["ontology_before_commitment"]),
            "ontology_after_commitment": entry["ontology_after_commitment"],
            "transformation_commitment": str(entry["transformation_commitment"]),
            "lost_distinction_hashes": self._decoded_array(str(entry["lost_distinction_hashes_json"])),
            "excluded_future_hashes": self._decoded_array(str(entry["excluded_future_hashes_json"])),
            "required_primitive_ids": self._decoded_array(str(entry["required_primitive_ids_json"])),
            "commitment_refs": self._decoded_array(str(entry["commitment_refs_json"])),
            "outcome_refs": self._decoded_array(str(entry["outcome_refs_json"])),
            "irrecoverability": None if irrec is None else float(irrec),
            "causal_efficacy": None if causal is None else float(causal),
            "autobiographic_time": None if auto_time is None else int(auto_time),
            "confidence": None if conf is None else float(conf),
            "reason_codes": self._decoded_array(str(entry["reason_codes_json"])),
            "policy_hash": str(entry["policy_hash"]),
            "schema_version": int(entry["schema_version"]),
        }

    def _evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        evaluability = str(evaluation.get("evaluability", ""))
        if evaluability not in ("observed", "not_evaluable"):
            raise RuntimeError("aporia_autobiography_evaluation_invalid")

        lost = self._hashes(evaluation.get("lost_distinction_hashes"))
        excluded = self._hashes(evaluation.get("excluded_future_hashes"))
        reason_codes = evaluation.get("reason_codes")
        if not isinstance(reason_codes, list) or not reason_codes:
            raise RuntimeError("aporia_autobiography_evaluation_invalid")

        for code in reason_codes:
            if not isinstance(code, str) or not re.match(r"^[a-z0-9_]{3,80}$", code):
                raise RuntimeError("aporia_autobiography_evaluation_invalid")

        reason_list = list(reason_codes)
        for boundary in ("structural_shadow_only", "content_not_persisted"):
            if boundary not in reason_list:
                reason_list.append(boundary)

        if evaluability == "not_evaluable" and (lost or excluded):
            raise RuntimeError("aporia_autobiography_evaluation_invalid")

        uniq_reasons = []
        for r in reason_list:
            if r not in uniq_reasons:
                uniq_reasons.append(r)

        return {
            "evaluability": evaluability,
            "lost_distinction_hashes": lost,
            "excluded_future_hashes": excluded,
            "irrecoverability": self._nullable_metric(evaluation.get("irrecoverability")),
            "causal_efficacy": self._nullable_metric(evaluation.get("causal_efficacy")),
            "confidence": self._nullable_metric(evaluation.get("confidence")),
            "reason_codes": uniq_reasons,
        }

    def _hashes(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            raise RuntimeError("aporia_autobiography_evaluation_invalid")
        result = []
        for val in values:
            if not isinstance(val, str) or not re.match(r"^[0-9a-f]{64}$", val, re.IGNORECASE):
                raise RuntimeError("aporia_autobiography_evaluation_invalid")
            result.append(val.lower())
        return sorted(list(set(result)))

    def _nullable_metric(self, value: Any) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RuntimeError("aporia_autobiography_evaluation_invalid")
        fv = float(value)
        if not math.isfinite(fv) or fv < 0.0 or fv > 1.0:
            raise RuntimeError("aporia_autobiography_evaluation_invalid")
        return fv

    def _public_run_id(self, tenant_id: int, run_row_id: int) -> str:
        stmt = self.conn.prepare("SELECT run_id FROM aporia_lacuna_runs WHERE tenant_id = ? AND id = ? LIMIT 1")
        stmt.execute([tenant_id, run_row_id])
        run_id = stmt.fetch_column()
        if not run_id:
            raise RuntimeError("aporia_autobiography_run_unavailable")
        return str(run_id)

    def _decoded_array(self, json_str: str) -> list[Any]:
        val = json.loads(json_str)
        if not isinstance(val, list):
            raise RuntimeError("aporia_autobiography_payload_invalid")
        return val

    def _signature(self, tenant_id: int, event_id: str, prev_sig: str, payload_hash: str) -> str:
        msg = f"{tenant_id}|{event_id}|{prev_sig}|{payload_hash}"
        return hmac.new(self.secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

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


class AporiaAutobiographyEvidenceInstrument:
    MIN_CONTEXT_SUPPORT = 3
    SMOOTHING = 0.5

    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_autobiography_evidence_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def observe(self, source: dict[str, Any]) -> dict[str, Any]:
        tenant_id = int(source.get("tenant_id", 0))
        event_row_id = int(source.get("id", 0))
        event_kind = str(source.get("event_kind", ""))

        if tenant_id < 1 or event_row_id < 1 or event_kind not in AporiaCausalEventSemanticsV2.event_kinds():
            raise RuntimeError("aporia_autobiography_evidence_scope_invalid")

        pred_kind = self._predecessor_kind(tenant_id, event_row_id)
        before = self._transition_counts(tenant_id, event_row_id)
        after = {k: dict(v) for k, v in before.items()}
        if pred_kind not in after:
            after[pred_kind] = {}
        after[pred_kind][event_kind] = after[pred_kind].get(event_kind, 0) + 1

        return {
            "hypothesis_pairs": self._hypothesis_pairs(tenant_id, before, after),
            "futures_before": self._future_commitments(tenant_id, pred_kind),
            "futures_after": self._future_commitments(tenant_id, event_kind),
            "predecessor_event_kind": pred_kind,
            "instrument_version": 1,
        }

    def _predecessor_kind(self, tenant_id: int, event_row_id: int) -> str:
        stmt = self.conn.prepare(
            """SELECT parent.event_kind
             FROM aporia_event_parents edge
             INNER JOIN aporia_events parent
               ON parent.tenant_id = edge.tenant_id AND parent.id = edge.parent_event_id
             WHERE edge.tenant_id = ? AND edge.child_event_id = ?
               AND edge.relation_type = 'session_predecessor' LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        kind = stmt.fetch_column()
        return str(kind) if kind not in (None, False, "") else "root"

    def _transition_counts(self, tenant_id: int, before_event_row_id: int) -> dict[str, dict[str, int]]:
        stmt = self.conn.prepare(
            """SELECT COALESCE(parent.event_kind, 'root') AS predecessor_kind,
                    child.event_kind, COUNT(*) AS transition_count
             FROM aporia_events child
             LEFT JOIN aporia_event_parents edge
               ON edge.tenant_id = child.tenant_id AND edge.child_event_id = child.id
              AND edge.relation_type = 'session_predecessor'
             LEFT JOIN aporia_events parent
               ON parent.tenant_id = edge.tenant_id AND parent.id = edge.parent_event_id
             WHERE child.tenant_id = ? AND child.id < ?
             GROUP BY COALESCE(parent.event_kind, 'root'), child.event_kind
             ORDER BY predecessor_kind, child.event_kind"""
        )
        stmt.execute([tenant_id, before_event_row_id])
        counts: dict[str, dict[str, int]] = {}
        valid_kinds = AporiaCausalEventSemanticsV2.event_kinds()
        for row in stmt.fetch_all() or []:
            predecessor = str(row["predecessor_kind"])
            event_kind = str(row["event_kind"])
            if (predecessor != "root" and predecessor not in valid_kinds) or event_kind not in valid_kinds:
                continue
            if predecessor not in counts:
                counts[predecessor] = {}
            counts[predecessor][event_kind] = int(row["transition_count"])
        return counts

    def _hypothesis_pairs(
        self, tenant_id: int, before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]
    ) -> list[dict[str, Any]]:
        contexts = []
        for ctx, c_map in before.items():
            if sum(c_map.values()) >= self.MIN_CONTEXT_SUPPORT:
                contexts.append(ctx)
        contexts.sort()

        pairs = []
        for left in range(len(contexts)):
            for right in range(left + 1, len(contexts)):
                left_ctx = contexts[left]
                right_ctx = contexts[right]
                p_hash = hmac.new(
                    self.secret.encode("utf-8"),
                    f"{tenant_id}|transition-hypothesis|{left_ctx}|{right_ctx}|v1".encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                pairs.append({
                    "pair_hash": p_hash,
                    "before_left": self._distribution(before.get(left_ctx, {})),
                    "before_right": self._distribution(before.get(right_ctx, {})),
                    "after_left": self._distribution(after.get(left_ctx, {})),
                    "after_right": self._distribution(after.get(right_ctx, {})),
                })
        return pairs

    def _distribution(self, counts: dict[str, int]) -> list[float]:
        return [float(counts.get(kind, 0)) + self.SMOOTHING for kind in AporiaCausalEventSemanticsV2.event_kinds()]

    def _future_commitments(self, tenant_id: int, context_event_kind: str) -> list[str]:
        produces = AporiaCausalEventSemanticsV2.produces_mask(context_event_kind)
        futures = []
        for candidate in AporiaCausalEventSemanticsV2.event_kinds():
            requires = AporiaCausalEventSemanticsV2.requires_any_mask(candidate)
            if requires != 0 and (produces & requires) == 0:
                continue
            msg = f"{tenant_id}|structural-future|{candidate}|v1"
            futures.append(hmac.new(self.secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest())
        futures.sort()
        return futures


class AporiaNegativeAutobiographyEvaluator:
    DELTA_HIGH = 0.50
    DELTA_LOW = 0.15

    def evaluate(
        self, hypothesis_pairs: list[dict[str, Any]], futures_before: list[str], futures_after: list[str]
    ) -> dict[str, Any]:
        lost = []
        for pair in hypothesis_pairs:
            if not isinstance(pair, dict):
                raise RuntimeError("aporia_autobiography_hypothesis_pair_invalid")
            pair_hash = str(pair.get("pair_hash", ""))
            self._assert_hash(pair_hash)

            b_left = pair.get("before_left") if isinstance(pair.get("before_left"), list) else []
            b_right = pair.get("before_right") if isinstance(pair.get("before_right"), list) else []
            a_left = pair.get("after_left") if isinstance(pair.get("after_left"), list) else []
            a_right = pair.get("after_right") if isinstance(pair.get("after_right"), list) else []

            before_js = self.jensen_shannon(b_left, b_right)
            after_js = self.jensen_shannon(a_left, a_right)

            if before_js >= self.DELTA_HIGH and after_js <= self.DELTA_LOW:
                lost.append(pair_hash.lower())

        f_before = self._hash_set(futures_before)
        f_after = self._hash_set(futures_after)
        excluded = sorted(list(set(f_before) - set(f_after)))
        lost = sorted(list(set(lost)))

        evaluability = "not_evaluable" if not hypothesis_pairs and not futures_before else "observed"
        reason_codes = ["hypotheses_and_futures_not_instrumented"]
        if evaluability == "observed":
            reason_codes = []
            if hypothesis_pairs:
                reason_codes.append("hypothesis_separability_observed")
            if futures_before:
                reason_codes.append("structural_future_exclusion_observed")
            reason_codes.append("irrecoverability_not_instrumented")
            reason_codes.append("causal_efficacy_not_instrumented")

        return {
            "evaluability": evaluability,
            "lost_distinction_hashes": lost,
            "excluded_future_hashes": excluded,
            "distinction_loss_rate": None if not hypothesis_pairs else len(lost) / len(hypothesis_pairs),
            "future_exclusion_rate": None if not futures_before else len(excluded) / len(futures_before),
            "irrecoverability": None,
            "causal_efficacy": None,
            "confidence": None,
            "reason_codes": reason_codes,
            "thresholds": {"delta_high": self.DELTA_HIGH, "delta_low": self.DELTA_LOW},
            "version": 1,
        }

    def jensen_shannon(self, left: list[float], right: list[float]) -> float:
        l_dist = self._distribution(left)
        r_dist = self._distribution(right)
        if len(l_dist) != len(r_dist) or not l_dist:
            raise RuntimeError("aporia_autobiography_distribution_invalid")
        midpoint = [(l_dist[i] + r_dist[i]) / 2.0 for i in range(len(l_dist))]
        return (self._kullback_leibler(l_dist, midpoint) + self._kullback_leibler(r_dist, midpoint)) / 2.0

    def _distribution(self, values: Any) -> list[float]:
        if not isinstance(values, list) or not values:
            raise RuntimeError("aporia_autobiography_distribution_invalid")
        total = 0.0
        normalized = []
        for val in values:
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise RuntimeError("aporia_autobiography_distribution_invalid")
            fv = float(val)
            if not math.isfinite(fv) or fv < 0.0:
                raise RuntimeError("aporia_autobiography_distribution_invalid")
            normalized.append(fv)
            total += fv
        if total <= 0.0:
            raise RuntimeError("aporia_autobiography_distribution_invalid")
        return [v / total for v in normalized]

    def _kullback_leibler(self, left: list[float], right: list[float]) -> float:
        val = 0.0
        for i, p in enumerate(left):
            if p <= 0.0:
                continue
            if right[i] <= 0.0:
                raise RuntimeError("aporia_autobiography_distribution_invalid")
            val += p * math.log2(p / right[i])
        return val

    def _hash_set(self, values: list[Any]) -> list[str]:
        result = []
        for val in values:
            if not isinstance(val, str):
                raise RuntimeError("aporia_autobiography_future_invalid")
            self._assert_hash(val)
            result.append(val.lower())
        return sorted(list(set(result)))

    def _assert_hash(self, value: str) -> None:
        if not re.match(r"^[0-9a-f]{64}$", value, re.IGNORECASE):
            raise RuntimeError("aporia_autobiography_hash_invalid")


class AporiaNegativeAutobiographyLedger:
    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_negative_autobiography_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def record(self, tenant_id: int, autobiography_entry_id: str, evaluation: dict[str, Any], created_at: str) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_negative_autobiography_transaction_required")
        if tenant_id < 1 or not autobiography_entry_id or not created_at:
            raise RuntimeError("aporia_negative_autobiography_scope_invalid")

        entry_stmt = self.conn.prepare(
            "SELECT entry_signature, causal_event_id FROM aporia_autobiography_entries WHERE tenant_id = ? AND entry_id = ? LIMIT 1"
        )
        entry_stmt.execute([tenant_id, autobiography_entry_id])
        entry = entry_stmt.fetch()
        if not entry:
            raise RuntimeError("aporia_negative_autobiography_entry_unavailable")

        existing = self.conn.prepare(
            "SELECT evaluation_id, evaluation_signature FROM aporia_negative_autobiography_evaluations WHERE tenant_id = ? AND autobiography_entry_id = ? LIMIT 1"
        )
        existing.execute([tenant_id, autobiography_entry_id])
        row = existing.fetch()
        if row:
            return {
                "evaluation_id": str(row["evaluation_id"]),
                "evaluation_signature": str(row["evaluation_signature"]),
                "deduplicated": True,
            }

        payload = self._payload(evaluation)
        payload_hash = hashlib.sha256(self._json(payload).encode("utf-8")).hexdigest()
        eval_hash = hashlib.sha256(f"{tenant_id}|{autobiography_entry_id}|negative-autobiography-v1".encode("utf-8")).hexdigest()
        evaluation_id = self._uuid(eval_hash)

        sig_msg = f"{tenant_id}|{autobiography_entry_id}|{entry['entry_signature']}|{payload_hash}"
        signature = hmac.new(self.secret.encode("utf-8"), sig_msg.encode("utf-8"), hashlib.sha256).hexdigest()

        reason_code = (
            "separability_and_future_exclusion_observed"
            if payload["evaluability"] == "observed"
            else "hypotheses_and_futures_not_instrumented"
        )

        op_id = hashlib.sha256(f"{tenant_id}|{evaluation_id}|negative-evaluation".encode("utf-8")).hexdigest()

        stmt = self.conn.prepare(
            """INSERT INTO aporia_negative_autobiography_evaluations
             (evaluation_id, tenant_id, autobiography_entry_id, causal_event_id, evaluability,
              lost_distinction_hashes_json, excluded_future_hashes_json, distinction_loss_rate,
              future_exclusion_rate, irrecoverability, causal_efficacy, confidence, reason_codes_json,
              delta_high, delta_low, payload_hash, evaluation_signature, event_name, event_version,
              environment, stream, category, component, operation_id, actor_type, action_name,
              lifecycle_phase, outcome, reason_code, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?,
                     'aporia.autobiography.negative_evaluation.recorded', 1, ?, 'system', 'audit',
                     'aporia-negative-autobiography-ledger', ?, 'worker', 'record_negative_evaluation',
                     'succeeded', 'succeeded', ?, 1, ?, ?)"""
        )
        dist_rate = None if payload["distinction_loss_rate"] is None else f"{payload['distinction_loss_rate']:.12f}"
        fut_rate = None if payload["future_exclusion_rate"] is None else f"{payload['future_exclusion_rate']:.12f}"

        stmt.execute([
            evaluation_id,
            tenant_id,
            autobiography_entry_id,
            entry["causal_event_id"],
            payload["evaluability"],
            self._json(payload["lost_distinction_hashes"]),
            self._json(payload["excluded_future_hashes"]),
            dist_rate,
            fut_rate,
            self._json(payload["reason_codes"]),
            f"{payload['thresholds']['delta_high']:.12f}",
            f"{payload['thresholds']['delta_low']:.12f}",
            payload_hash,
            signature,
            self._environment(),
            op_id,
            reason_code,
            created_at,
            created_at,
        ])

        return {
            "evaluation_id": evaluation_id,
            "evaluation_signature": signature,
            "deduplicated": False,
        }

    def _payload(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        required = [
            "evaluability",
            "lost_distinction_hashes",
            "excluded_future_hashes",
            "distinction_loss_rate",
            "future_exclusion_rate",
            "irrecoverability",
            "causal_efficacy",
            "confidence",
            "reason_codes",
            "thresholds",
            "version",
        ]
        keys = sorted(list(evaluation.keys()))
        if (
            keys != sorted(required)
            or evaluation.get("evaluability") not in ("observed", "not_evaluable")
            or evaluation.get("irrecoverability") is not None
            or evaluation.get("causal_efficacy") is not None
            or evaluation.get("confidence") is not None
            or evaluation.get("version") != 1
            or not isinstance(evaluation.get("lost_distinction_hashes"), list)
            or not isinstance(evaluation.get("excluded_future_hashes"), list)
            or not isinstance(evaluation.get("reason_codes"), list)
            or not isinstance(evaluation.get("thresholds"), dict)
        ):
            raise RuntimeError("aporia_negative_autobiography_evaluation_invalid")

        for h in evaluation["lost_distinction_hashes"] + evaluation["excluded_future_hashes"]:
            if not isinstance(h, str) or not re.match(r"^[0-9a-f]{64}$", h):
                raise RuntimeError("aporia_negative_autobiography_evaluation_invalid")

        return evaluation

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
