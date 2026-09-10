"""
Logical Lacuna Evaluator and Processor for APORIA.
Replicates AporiaLacunaLogical with exact mathematical and cryptographic invariants.
"""

from __future__ import annotations
import os
import math
import json
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from typing import Any, Sequence

from aporia.crypto import canonical_json
from aporia.infrastructure.causal_semantics import AporiaCausalEventSemanticsV2
from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernel, AporiaLacunaKernelV2
from aporia.infrastructure.lacuna_crypto_runner import AporiaLacunaCryptographicRunner
from aporia.infrastructure.autobiography import (
    AporiaAutobiographyLedger,
    AporiaAutobiographyEvidenceInstrument,
    AporiaNegativeAutobiographyEvaluator,
    AporiaNegativeAutobiographyLedger,
)
from aporia.infrastructure.ontology import AporiaOntologyResidualCollector
from aporia.infrastructure.identity_continuity import AporiaIdentityLedger
from aporia.infrastructure.experiments import AporiaAdvancedRuntimeLedger


class AporiaLacunaLogical:
    """Logical shadow pipeline for evaluating Lacuna mechanisms."""

    MAX_ATTEMPTS = 8
    ALGORITHM_VERSION = 2
    FEATURE_SPACE_VERSION = 2
    PROJECTION_VERSION = 2

    PERSPECTIVES = [
        "causal_continuity",
        "epistemic_provenance",
        "operational_state",
        "relationship_commitment",
        "outcome_learning",
        "identity_continuity",
    ]

    INSTRUMENTS = [
        AporiaLacunaKernelV2.REQUIREMENT_FOCUS,
        AporiaLacunaKernelV2.DEPENDENCY_PROJECTION,
    ]

    def __init__(self, pdo: Any, secret: str, sidecar_path: str | None = None) -> None:
        if not secret or secret.strip() == "":
            raise RuntimeError("aporia_lacuna_secret_required")
        self.pdo = pdo
        self.secret = secret
        if sidecar_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.sidecar_path = os.path.join(base_dir, "experiments", "aporia-lacuna", "sidecar", "one_shot.py")
        else:
            self.sidecar_path = sidecar_path

    def process_next(self, tenant_ids: Sequence[int] | None = None) -> bool:
        """Processes the next pending item in the outbox."""
        clean_tenants = []
        if tenant_ids:
            clean_tenants = sorted(list({int(t) for t in tenant_ids if int(t) > 0}))
        
        scope = ""
        if clean_tenants:
            placeholders = ",".join("?" for _ in clean_tenants)
            scope = f" AND tenant_id IN ({placeholders})"

        sql = f"""SELECT tenant_id, source_event_id
             FROM aporia_lacuna_outbox
             WHERE algorithm_version = {self.ALGORITHM_VERSION} AND status = 'pending'
               AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
             {scope}
             ORDER BY COALESCE(next_attempt_at, created_at), id
             LIMIT 1"""

        stmt = self.pdo.prepare(sql)
        stmt.execute(clean_tenants)
        candidate = stmt.fetch()
        if not candidate:
            return False

        self.process(int(candidate["tenant_id"]), int(candidate["source_event_id"]))
        return True

    def processNext(self, tenant_ids: Sequence[int] | None = None) -> bool:
        return self.process_next(tenant_ids)

    def process(self, tenant_id: int, event_row_id: int) -> dict[str, bool]:
        """Runs the logical Lacuna pipeline for a specific event."""
        if tenant_id < 1 or event_row_id < 1:
            raise RuntimeError("aporia_lacuna_scope_invalid")

        owner_token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        self.pdo.begin_transaction()
        try:
            source = self._claim(tenant_id, event_row_id, owner_token_hash)
            if source.get("status") == "completed":
                self.pdo.commit()
                return {"completed": True, "deduplicated": True}

            snapshot = self._snapshot(source)
            run_id = self._uuid(
                hashlib.sha256(f"{tenant_id}|{source['event_id']}|lacuna-logical|{self.ALGORITHM_VERSION}".encode("utf-8")).hexdigest()
            )
            control_seed = hashlib.sha256(
                f"{tenant_id}|{source['event_id']}|control|{self.ALGORITHM_VERSION}".encode("utf-8")
            ).hexdigest()

            run_row_id = self._insert_run(source, snapshot, run_id, control_seed)

            if snapshot["interpretation_status"] != "not_evaluable":
                self._persist_evaluation(source, snapshot, run_row_id, run_id, control_seed)
            else:
                for metric in [
                    "order_sensitivity",
                    "structural_order_evidence",
                    "logical_loss_normalized",
                    "irrecoverability",
                    "causal_effectiveness",
                    "autobiographic_time",
                ]:
                    self._insert_measurement(
                        source,
                        run_row_id,
                        metric,
                        None,
                        "turns" if metric == "autobiographic_time" else "ratio",
                        "not_evaluable",
                        snapshot["reason_codes"],
                        [],
                    )

            autobiography_evidence = AporiaAutobiographyEvidenceInstrument(self.pdo, self.secret).observe(source)
            negative_evaluation = AporiaNegativeAutobiographyEvaluator().evaluate(
                autobiography_evidence["hypothesis_pairs"],
                autobiography_evidence["futures_before"],
                autobiography_evidence["futures_after"],
            )
            autobiography = AporiaAutobiographyLedger(self.pdo, self.secret).record(
                source,
                run_row_id,
                run_id,
                snapshot["input_commitment"],
                negative_evaluation,
            )
            AporiaNegativeAutobiographyLedger(self.pdo, self.secret).record(
                tenant_id,
                autobiography["entry_id"],
                negative_evaluation,
                str(source["ingested_at"]),
            )
            if isinstance(snapshot.get("vector"), list):
                AporiaOntologyResidualCollector(self.pdo, self.secret).record_and_propose(
                    source,
                    run_row_id,
                    run_id,
                    snapshot["vector"],
                    snapshot["input_commitment"],
                    str(source["ingested_at"]),
                )

            AporiaIdentityLedger(self.pdo, self.secret).record_snapshot(
                tenant_id,
                autobiography["entry_id"],
                str(source["ingested_at"]),
            )
            AporiaAdvancedRuntimeLedger(
                self.pdo,
                self.secret,
                str(os.getenv("APP_ENV", "unknown")),
            ).record(source, run_row_id, run_id)

            self._complete(source, owner_token_hash)
            self.pdo.commit()
            return {"completed": True, "deduplicated": False}
        except Exception as exc:
            if hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            try:
                self._defer_failure(tenant_id, event_row_id, exc)
            except Exception:
                pass
            raise exc

    def consume_capability(self, tenant_id: int, branch_id: str, token: str) -> None:
        """Consumes a branch capability."""
        if tenant_id < 1 or not branch_id or not branch_id.strip() or not token or not token.strip():
            raise RuntimeError("aporia_lacuna_capability_invalid")

        self.pdo.begin_transaction()
        try:
            stmt = self.pdo.prepare(
                "SELECT capability_hash, capability_status FROM aporia_lacuna_branches "
                "WHERE tenant_id = ? AND branch_id = ? LIMIT 1"
            )
            stmt.execute([tenant_id, branch_id])
            branch = stmt.fetch()
            if not branch:
                raise RuntimeError("aporia_lacuna_capability_not_found")

            if str(branch.get("capability_status")) != "ready":
                raise RuntimeError("aporia_lacuna_capability_consumed")

            expected_hash = self._capability_hash(tenant_id, branch_id, token)
            if not hmac.compare_digest(str(branch.get("capability_hash")), expected_hash):
                raise RuntimeError("aporia_lacuna_capability_invalid")

            update = self.pdo.prepare(
                "UPDATE aporia_lacuna_branches SET capability_status = 'consumed', updated_at = CURRENT_TIMESTAMP "
                "WHERE tenant_id = ? AND branch_id = ? AND capability_status = 'ready'"
            )
            update.execute([tenant_id, branch_id])
            if update.rowcount != 1:
                raise RuntimeError("aporia_lacuna_capability_consumed")
            self.pdo.commit()
        except Exception as exc:
            if hasattr(self.pdo, "in_transaction") and self.pdo.in_transaction():
                self.pdo.roll_back()
            raise exc

    def consumeCapability(self, tenant_id: int, branch_id: str, token: str) -> None:
        self.consume_capability(tenant_id, branch_id, token)

    def _claim(self, tenant_id: int, event_row_id: int, owner_token_hash: str) -> dict[str, Any]:
        stmt = self.pdo.prepare(
            f"""SELECT event.id, event.event_id, event.session_ref, event.task_ref, event.event_kind, event.causal_depth, event.completeness,
                    event.hlc_wall_us, event.hlc_logical, event.ingested_at,
                    envelope.envelope_id, envelope.observation_ids_json, envelope.delivery_mode,
                    envelope.completeness AS envelope_completeness, envelope.policy_hash,
                    outbox.status, outbox.fencing_token
             FROM aporia_events event
             INNER JOIN aporia_shadow_envelopes envelope
               ON envelope.tenant_id = event.tenant_id AND envelope.event_id = event.id
             INNER JOIN aporia_lacuna_outbox outbox
               ON outbox.tenant_id = event.tenant_id AND outbox.source_event_id = event.id
              AND outbox.envelope_id = envelope.envelope_id
             WHERE event.tenant_id = ? AND event.id = ? AND outbox.algorithm_version = {self.ALGORITHM_VERSION}
             LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        source = stmt.fetch()
        if not source:
            raise RuntimeError("aporia_lacuna_source_not_found")

        source["tenant_id"] = tenant_id
        if str(source.get("status")) == "completed":
            return source

        if str(source.get("status")) != "pending":
            raise RuntimeError("aporia_lacuna_unavailable")

        fencing_token = int(source.get("fencing_token", 0)) + 1
        now = datetime.now(timezone.utc)
        lease_expires_at = (now + timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S.%f")

        update = self.pdo.prepare(
            f"""UPDATE aporia_lacuna_outbox
             SET status = 'leased', fencing_token = ?, owner_token_hash = ?, lease_expires_at = ?, updated_at = ?
             WHERE tenant_id = ? AND source_event_id = ? AND algorithm_version = {self.ALGORITHM_VERSION} AND status = 'pending'"""
        )
        update.execute([
            fencing_token,
            owner_token_hash,
            lease_expires_at,
            source["ingested_at"],
            tenant_id,
            event_row_id,
        ])
        if update.rowcount != 1:
            raise RuntimeError("aporia_lacuna_claim_failed")

        source["fencing_token"] = fencing_token
        source["status"] = "leased"
        return source

    def _snapshot(self, source: dict[str, Any]) -> dict[str, Any]:
        reason_codes: list[str] = []
        if str(source.get("delivery_mode")) != "shadow":
            reason_codes.append("delivery_mode_unsupported")

        if str(source.get("completeness")) != "complete" or str(source.get("envelope_completeness")) != "complete":
            reason_codes.append("source_incomplete")

        try:
            expected_ids = json.loads(str(source.get("observation_ids_json", "[]")))
        except Exception:
            expected_ids = []

        if not isinstance(expected_ids, list) or len(expected_ids) != len(self.PERSPECTIVES):
            reason_codes.append("observation_set_invalid")
            expected_ids = []

        stmt = self.pdo.prepare(
            "SELECT observation_id, perspective, epistemic_status, uncertainty_codes_json, value_json, policy_hash, valid_until "
            "FROM aporia_perspective_observations "
            "WHERE tenant_id = ? AND event_id = ? AND projection_version = ? ORDER BY perspective"
        )
        stmt.execute([source["tenant_id"], source["id"], self.PROJECTION_VERSION])
        rows = stmt.fetchAll() or []
        observations: dict[str, dict[str, Any]] = {}
        for row in rows:
            observations[str(row["perspective"])] = row

        actual_ids = sorted([str(r["observation_id"]) for r in rows])
        expected_ids_sorted = sorted([str(x) for x in expected_ids])
        actual_perspectives = sorted(list(observations.keys()))
        expected_perspectives = sorted(list(self.PERSPECTIVES))

        if actual_ids != expected_ids_sorted or actual_perspectives != expected_perspectives:
            reason_codes.append("observation_set_invalid")

        unknown_count = 0
        for obs in observations.values():
            if str(obs.get("policy_hash")) != str(source.get("policy_hash")):
                reason_codes.append("policy_hash_mismatch")
            if obs.get("valid_until") is not None:
                reason_codes.append("observation_expired_or_bounded")
            if str(obs.get("epistemic_status")) == "unknown":
                unknown_count += 1

        if reason_codes:
            unique_codes = sorted(list(set(reason_codes)), key=reason_codes.index)
            invalid_commitment = hashlib.sha256(f"{source['tenant_id']}|{source['event_id']}|invalid".encode("utf-8")).hexdigest()
            return {
                "interpretation_status": "not_evaluable",
                "reason_codes": unique_codes,
                "input_commitment": invalid_commitment,
                "vector": None,
            }

        causal_raw = observations["causal_continuity"].get("value_json", "{}")
        try:
            causal = json.loads(str(causal_raw))
        except Exception:
            raise RuntimeError("aporia_lacuna_observation_invalid")

        if not isinstance(causal, dict):
            raise RuntimeError("aporia_lacuna_observation_invalid")

        predecessor_event_kind = str(causal.get("predecessor_event_kind", ""))
        try:
            predecessor_produces_mask = AporiaCausalEventSemanticsV2.produces_mask(predecessor_event_kind)
            current_requires_mask = AporiaCausalEventSemanticsV2.requires_any_mask(str(source.get("event_kind", "")))
            current_code = AporiaCausalEventSemanticsV2.code(str(source.get("event_kind", "")))
            predecessor_code = AporiaCausalEventSemanticsV2.code(predecessor_event_kind)
        except RuntimeError:
            unsupported_commitment = hashlib.sha256(f"{source['tenant_id']}|{source['event_id']}|unsupported".encode("utf-8")).hexdigest()
            return {
                "interpretation_status": "not_evaluable",
                "reason_codes": ["causal_event_semantics_unsupported"],
                "input_commitment": unsupported_commitment,
                "vector": None,
            }

        vector = [
            unknown_count / float(len(self.PERSPECTIVES)),
            float(predecessor_produces_mask),
            float(current_requires_mask),
            min(1.0, max(0.0, float(causal.get("parent_count", 0)))),
            min(1.0, max(0.0, float(causal.get("causal_depth", 0)) / 100.0)),
            float(current_code),
            float(predecessor_code),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ]
        while len(vector) < 32:
            vector.append(0.0)

        input_commitment = hashlib.sha256(
            self._json({
                "feature_space_version": self.FEATURE_SPACE_VERSION,
                "source_envelope_id": source["envelope_id"],
                "source_hlc": [int(source["hlc_wall_us"]), int(source["hlc_logical"])],
                "vector": vector,
            }).encode("utf-8")
        ).hexdigest()

        return {
            "interpretation_status": "unknown" if unknown_count > 0 else "observed",
            "reason_codes": ["logical_shadow_only", "source_reconstructible"],
            "input_commitment": input_commitment,
            "vector": vector,
        }

    def _insert_run(self, source: dict[str, Any], snapshot: dict[str, Any], run_id: str, control_seed: str) -> int:
        stmt = self.pdo.prepare(
            f"""INSERT INTO aporia_lacuna_runs
             (run_id, tenant_id, source_event_id, envelope_id, algorithm_version, feature_space_version, policy_hash,
              input_commitment, control_seed, execution_status, interpretation_status, reason_codes_json,
              completed_at, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, {self.ALGORITHM_VERSION}, {self.FEATURE_SPACE_VERSION}, ?, ?, ?, 'completed', ?, ?, ?, 1, ?, ?)"""
        )
        stmt.execute([
            run_id,
            source["tenant_id"],
            source["id"],
            source["envelope_id"],
            source["policy_hash"],
            snapshot["input_commitment"],
            control_seed,
            snapshot["interpretation_status"],
            self._json(snapshot["reason_codes"]),
            source["ingested_at"],
            source["ingested_at"],
            source["ingested_at"],
        ])
        return int(self.pdo.last_insert_id())

    def _persist_evaluation(
        self,
        source: dict[str, Any],
        snapshot: dict[str, Any],
        run_row_id: int,
        run_id: str,
        control_seed: str,
    ) -> None:
        input_vec = list(snapshot["vector"])
        attention_then_causal = self._apply_instrument(
            self._apply_instrument(input_vec, self.INSTRUMENTS[0]), self.INSTRUMENTS[1]
        )
        causal_then_attention = self._apply_instrument(
            self._apply_instrument(input_vec, self.INSTRUMENTS[1]), self.INSTRUMENTS[0]
        )
        differences = self._differences(input_vec, attention_then_causal)
        changed = len(differences)

        branches = [
            ("q_then_r", "lacuna", list(self.INSTRUMENTS), attention_then_causal),
            ("r_then_q", "lacuna", list(reversed(self.INSTRUMENTS)), causal_then_attention),
            ("keep", "keep", [], input_vec),
            ("destroy", "destroy", [], [-1.0] * len(input_vec)),
            ("random", "random", [], self._random_control(input_vec, differences, control_seed)),
            ("passive", "passive", [], input_vec),
            ("order_only", "order_only", list(self.INSTRUMENTS), input_vec),
        ]

        branch_ids: dict[str, str] = {}
        for index, (name, kind, order, output) in enumerate(branches):
            branch_ids[name] = self._insert_branch(
                source,
                run_row_id,
                run_id,
                name,
                kind,
                order,
                input_vec,
                output,
                index + 1,
                changed if kind == "lacuna" else self._changed_dimensions(input_vec, output),
            )

        cryptographic_runner = AporiaLacunaCryptographicRunner(self.pdo, self.secret, self.sidecar_path)
        cryptographic_runner.execute_and_persist(
            int(source["tenant_id"]),
            run_row_id,
            run_id,
            "q_then_r_v2",
            input_vec,
            attention_then_causal,
            str(source["ingested_at"]),
        )
        cryptographic_runner.execute_and_persist(
            int(source["tenant_id"]),
            run_row_id,
            run_id,
            "r_then_q_v2",
            input_vec,
            causal_then_attention,
            str(source["ingested_at"]),
        )

        order_score = self._order_score(attention_then_causal, causal_then_attention)
        destroy_loss = self._loss(input_vec, branches[3][3])
        lacuna_loss = self._loss(input_vec, attention_then_causal)
        logical_loss = min(1.0, max(0.0, lacuna_loss / destroy_loss)) if destroy_loss > 1.0e-12 else None
        mechanism_status = "unknown" if snapshot["interpretation_status"] == "unknown" else "observed"
        mechanism_reasons = ["validated_by_preregistered_v2_corpus", "structural_shadow_only", "not_semantic_causality"]

        self._insert_measurement(
            source, run_row_id, "order_sensitivity", order_score, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["q_then_r"], branch_ids["r_then_q"]]
        )
        self._insert_measurement(
            source, run_row_id, "keep_control_loss", 0.0, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["keep"]]
        )
        self._insert_measurement(
            source, run_row_id, "destroy_control_loss", destroy_loss, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["destroy"]]
        )
        self._insert_measurement(
            source, run_row_id, "random_control_loss", self._loss(input_vec, branches[4][3]), "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["random"]]
        )
        self._insert_measurement(
            source, run_row_id, "lacuna_change_magnitude", sum(differences), "l1", mechanism_status, mechanism_reasons,
            [branch_ids["q_then_r"]]
        )
        self._insert_measurement(
            source, run_row_id, "random_control_magnitude", self._magnitude(input_vec, branches[4][3]), "l1", mechanism_status, mechanism_reasons,
            [branch_ids["random"]]
        )
        self._insert_measurement(
            source, run_row_id, "passive_control_order", 0.0, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["passive"]]
        )
        self._insert_measurement(
            source, run_row_id, "order_only_control", 0.0, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["order_only"]]
        )
        self._insert_measurement(
            source, run_row_id, "structural_order_evidence", order_score, "ratio", mechanism_status, mechanism_reasons,
            [branch_ids["q_then_r"], branch_ids["r_then_q"], branch_ids["random"], branch_ids["passive"], branch_ids["order_only"]]
        )
        self._insert_measurement(
            source, run_row_id, "logical_loss_normalized", logical_loss, "ratio",
            "not_evaluable" if logical_loss is None else mechanism_status,
            ["degenerate_destroy_denominator"] if logical_loss is None else ["not_irrecoverability", "structural_shadow_only"],
            [branch_ids["keep"], branch_ids["destroy"], branch_ids["q_then_r"]]
        )

        for metric in ["irrecoverability", "causal_effectiveness", "autobiographic_time"]:
            self._insert_measurement(
                source,
                run_row_id,
                metric,
                None,
                "turns" if metric == "autobiographic_time" else "ratio",
                "not_evaluable",
                [f"{metric}_not_instrumented"],
                [],
            )

    def _insert_branch(
        self,
        source: dict[str, Any],
        run_row_id: int,
        run_id: str,
        name: str,
        kind: str,
        order: list[str],
        input_vec: list[float],
        output_vec: list[float],
        operation_index: int,
        targeted_dimensions: int,
    ) -> str:
        branch_id = self._uuid(
            hashlib.sha256(f"{source['tenant_id']}|{run_id}|{name}".encode("utf-8")).hexdigest()
        )
        token = secrets.token_hex(32)
        stmt = self.pdo.prepare(
            """INSERT INTO aporia_lacuna_branches
             (branch_id, tenant_id, run_id, branch_name, control_kind, instrument_order_json, capability_hash,
              capability_status, input_commitment, output_commitment, changed_dimensions, targeted_dimensions,
              operation_index, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, 1, ?, ?)"""
        )
        cap_hash = self._capability_hash(int(source["tenant_id"]), branch_id, token)
        stmt.execute([
            branch_id,
            source["tenant_id"],
            run_row_id,
            name,
            kind,
            self._json(order),
            cap_hash,
            hashlib.sha256(self._json(input_vec).encode("utf-8")).hexdigest(),
            hashlib.sha256(self._json(output_vec).encode("utf-8")).hexdigest(),
            self._changed_dimensions(input_vec, output_vec),
            targeted_dimensions,
            operation_index,
            source["ingested_at"],
            source["ingested_at"],
        ])

        update = self.pdo.prepare(
            "UPDATE aporia_lacuna_branches SET capability_status = 'consumed', updated_at = ? "
            "WHERE tenant_id = ? AND branch_id = ? AND capability_status = 'ready' AND capability_hash = ?"
        )
        update.execute([
            source["ingested_at"],
            source["tenant_id"],
            branch_id,
            cap_hash,
        ])
        if update.rowcount != 1:
            raise RuntimeError("aporia_lacuna_capability_consume_failed")

        return branch_id

    def _insert_measurement(
        self,
        source: dict[str, Any],
        run_row_id: int,
        name: str,
        value: float | None,
        unit: str,
        evaluability: str,
        reason_codes: list[str],
        branch_ids: list[str],
    ) -> None:
        if value is not None and not math.isfinite(value):
            value = None
            evaluability = "not_evaluable"
            reason_codes = ["non_finite_metric"]

        stmt = self.pdo.prepare(
            """INSERT INTO aporia_lacuna_measurements
             (tenant_id, run_id, metric_name, metric_version, numeric_value, unit, evaluability,
              reason_codes_json, baseline_branch_ids_json, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, 1, ?, ?)"""
        )
        numeric_str = f"{value:.12f}" if value is not None else None
        stmt.execute([
            source["tenant_id"],
            run_row_id,
            name,
            numeric_str,
            unit,
            evaluability,
            self._json(reason_codes),
            self._json(branch_ids),
            source["ingested_at"],
            source["ingested_at"],
        ])

    def _complete(self, source: dict[str, Any], owner_token_hash: str) -> None:
        stmt = self.pdo.prepare(
            f"""UPDATE aporia_lacuna_outbox
             SET status = 'completed', completed_at = ?, owner_token_hash = NULL, lease_expires_at = NULL,
                 next_attempt_at = NULL, reason_code = NULL, updated_at = ?
             WHERE tenant_id = ? AND source_event_id = ? AND algorithm_version = {self.ALGORITHM_VERSION} AND status = 'leased'
               AND owner_token_hash = ? AND fencing_token = ?"""
        )
        stmt.execute([
            source["ingested_at"],
            source["ingested_at"],
            source["tenant_id"],
            source["id"],
            owner_token_hash,
            source["fencing_token"],
        ])
        if stmt.rowcount != 1:
            raise RuntimeError("aporia_lacuna_lease_lost")

    def _apply_instrument(self, state: list[float], instrument: str) -> list[float]:
        candidate = AporiaLacunaKernelV2.apply(state[:12], instrument)
        res = list(state)
        for idx, val in enumerate(candidate):
            res[idx] = val
        return res

    def _random_control(self, state: list[float], differences: list[float], seed: str) -> list[float]:
        ranked = {}
        for index in range(len(state)):
            ranked[index] = hashlib.sha256(f"{seed}|{index}".encode("utf-8")).hexdigest()
        
        # asort($ranked, SORT_STRING): sort keys ascending by hex digest
        sorted_indices = sorted(ranked.keys(), key=lambda i: ranked[i])
        sorted_diffs = sorted(differences, reverse=True)
        
        res = list(state)
        limit = min(len(sorted_diffs), len(res))
        for offset in range(limit):
            idx = sorted_indices[offset]
            res[idx] -= sorted_diffs[offset]
        return res

    def _differences(self, input_vec: list[float], output_vec: list[float]) -> list[float]:
        return AporiaLacunaKernel.differences(input_vec, output_vec)

    def _magnitude(self, input_vec: list[float], output_vec: list[float]) -> float:
        return AporiaLacunaKernel.magnitude(input_vec, output_vec)

    def _order_score(self, left: list[float], right: list[float]) -> float:
        return AporiaLacunaKernel.order_score(left, right)

    def _loss(self, input_vec: list[float], output_vec: list[float]) -> float:
        return AporiaLacunaKernel.loss(input_vec, output_vec)

    def _changed_dimensions(self, input_vec: list[float], output_vec: list[float]) -> int:
        return AporiaLacunaKernel.changed_dimensions(input_vec, output_vec)

    def _defer_failure(self, tenant_id: int, event_row_id: int, exc: Exception) -> None:
        stmt = self.pdo.prepare(
            f"""SELECT attempts FROM aporia_lacuna_outbox
             WHERE tenant_id = ? AND source_event_id = ? AND algorithm_version = {self.ALGORITHM_VERSION} AND status = 'pending' LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        attempts = stmt.fetchColumn(0)
        if attempts is False or attempts is None:
            return

        next_attempts = int(attempts) + 1
        status = "rejected" if next_attempts >= self.MAX_ATTEMPTS else "pending"
        delay_seconds = min(3600, 5 * (2 ** min(9, next_attempts - 1)))
        now = datetime.now(timezone.utc)
        
        msg = str(exc)
        if msg.startswith("aporia_crypto_"):
            reason_code = msg
        else:
            reason_code = f"lacuna_{exc.__class__.__name__.lower()}"[:80]

        next_attempt_at = (now + timedelta(seconds=delay_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f") if status == "pending" else None
        now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")

        update = self.pdo.prepare(
            f"""UPDATE aporia_lacuna_outbox
             SET status = ?, attempts = ?, next_attempt_at = ?, reason_code = ?, updated_at = ?
             WHERE tenant_id = ? AND source_event_id = ? AND algorithm_version = {self.ALGORITHM_VERSION} AND status = ?"""
        )
        update.execute([
            status,
            next_attempts,
            next_attempt_at,
            reason_code,
            now_str,
            tenant_id,
            event_row_id,
            "pending",
        ])

    def _capability_hash(self, tenant_id: int, branch_id: str, token: str) -> str:
        msg = f"{tenant_id}|{branch_id}|{token}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _uuid(self, hash_str: str) -> str:
        hex_s = hash_str[:32].lower()
        nibble = (int(hex_s[16], 16) & 0x3) | 0x8
        hex_list = list(hex_s)
        hex_list[12] = "5"
        hex_list[16] = format(nibble, "x")
        h = "".join(hex_list)
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
