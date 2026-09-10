"""
Aporia HIRT Gateways (Scientific and Product).
Evaluates proposed agent effects against HIRT hazard invariants,
verifies idempotency and reversibility, and enforces safety decisions.
"""

from __future__ import annotations
import json
import re
from typing import Any, Mapping, Sequence
from aporia.crypto import canonical_json, sha256_hex, hmac_sha256_hex, hash_equals
from aporia.infrastructure.db import Connection
from aporia.infrastructure.effect_compiler import AporiaEffectCompiler, AporiaProductEffectCompiler


class AporiaHirtGateway:
    TRANSFORMATIONS = (
        "H1_chronological_context",
        "H2_constraint_oriented_context",
        "H3_original_sources",
        "H4_materialized_state",
        "H5_high_level_tools",
    )

    def __init__(self, conn: Connection | Any, secret: str, environment: str = "unknown") -> None:
        if not secret.strip():
            raise ValueError("aporia_hirt_secret_required")
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.secret = secret
        self.environment = environment

    def inspect(
        self,
        tenant_id: int,
        episode_ref: str,
        action: str,
        parameters: Mapping[str, Any],
        authority_commitment: str,
        mode: str,
        cost_units: float = 1.0,
    ) -> dict[str, Any]:
        if (
            tenant_id < 1
            or not re.match(r"^[0-9a-f]{64}$", episode_ref)
            or mode not in ("shadow", "advisory", "guarded_reversible")
            or cost_units < 0.0
            or cost_units > 1_000_000.0
        ):
            raise ValueError("aporia_hirt_inspection_invalid")

        compiler = AporiaEffectCompiler()
        effects = []
        for trans in self.TRANSFORMATIONS:
            effects.append(compiler.compile(
                tenant_id,
                action,
                self._transform(dict(parameters), trans),
                authority_commitment,
            ))

        effect = effects[0]
        meet = compiler.meet(effects)
        operation_id = sha256_hex(f"{tenant_id}|{episode_ref}|{action}|{effect['effect_commitment']}|{mode}")

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare(
                "SELECT decision, allowed, reason_codes_json, safe_prefix_count, effect_commitment, meet_commitment "
                "FROM aporia_hirt_decisions WHERE tenant_id = ? AND operation_id = ? LIMIT 1"
            )
            stmt.execute([tenant_id, operation_id])
            row = stmt.fetch()
            if row:
                if started:
                    self.conn.commit()
                return {
                    "decision": str(row["decision"]),
                    "allowed": bool(row["allowed"]),
                    "reason_codes": json.loads(str(row["reason_codes_json"])),
                    "safe_prefix_count": int(row["safe_prefix_count"]),
                    "effect_commitment": str(row["effect_commitment"]),
                    "meet_commitment": str(row["meet_commitment"]),
                    "deduplicated": True,
                }

            decision, allowed, reasons = self._decision(tenant_id, episode_ref, effect, meet, mode, cost_units)
            reason_code = reasons[-1] if reasons else "hirt_decision_recorded"
            decision_id = self._uuid(sha256_hex(f"{operation_id}|decision"))

            stmt_ins = self.conn.prepare("""
                INSERT INTO aporia_hirt_decisions
                (decision_id, tenant_id, episode_ref, action_commitment, mode, effect_commitment, meet_commitment,
                 decision, allowed, reversible, external, risk, safe_prefix_count, reason_codes_json,
                 event_name, event_version, environment, stream, category, component, operation_id,
                 actor_type, action_name, lifecycle_phase, outcome, reason_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'aporia.hirt.effect.decision.recorded', 1, ?, 'system', 'audit', 'aporia-hirt-gateway', ?,
                        'worker', 'inspect_effect', ?, ?, ?)
            """)
            lifecycle = "succeeded" if allowed else "rejected"
            stmt_ins.execute([
                decision_id,
                tenant_id,
                episode_ref,
                sha256_hex(action),
                mode,
                effect["effect_commitment"],
                meet["meet_commitment"],
                decision,
                1 if allowed else 0,
                1 if effect["reversible"] else 0,
                1 if effect["external"] else 0,
                effect["risk"],
                len(meet["safe_prefix_atoms"]),
                json.dumps(reasons, separators=(",", ":")),
                self.environment[:24],
                operation_id,
                lifecycle,
                lifecycle,
                reason_code[:80],
            ])

            if allowed and mode == "guarded_reversible":
                self._increment_usage(tenant_id, cost_units)

            if started:
                self.conn.commit()

            return {
                "decision": decision,
                "allowed": allowed,
                "reason_codes": reasons,
                "safe_prefix_count": len(meet["safe_prefix_atoms"]),
                "effect_commitment": effect["effect_commitment"],
                "meet_commitment": meet["meet_commitment"],
                "deduplicated": False,
            }
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def _decision(
        self,
        tenant_id: int,
        episode_ref: str,
        effect: Mapping[str, Any],
        meet: Mapping[str, Any],
        mode: str,
        cost_units: float,
    ) -> tuple[str, bool, list[str]]:
        if mode == "shadow":
            return "shadow_observed", True, list(dict.fromkeys(list(meet.get("reason_codes", [])) + ["shadow_no_effect_authority"]))
        if mode == "advisory":
            return "advisory_only", True, list(dict.fromkeys(list(meet.get("reason_codes", [])) + ["advisory_no_effect_authority"]))
        if meet.get("deadlock"):
            return "evidence_required", False, list(dict.fromkeys(list(meet.get("reason_codes", [])) + ["hirt_meet_deadlock"]))
        stmt = self.conn.prepare(
            "SELECT arm FROM aporia_experiment_assignments "
            "WHERE tenant_id = ? AND episode_ref = ? AND experiment_key = 'aporia_guarded_reversible_v1' LIMIT 1"
        )
        stmt.execute([tenant_id, episode_ref])
        arm = stmt.fetch_column()
        if str(arm) != "C5":
            return "blocked", False, ["experiment_arm_not_guarded"]
        policy_stmt = self.conn.prepare("SELECT * FROM aporia_guarded_policies WHERE tenant_id = ? LIMIT 1")
        policy_stmt.execute([tenant_id])
        row = policy_stmt.fetch()
        if not row or str(row["status"]) != "active" or int(row["kill_switch"]) != 0:
            return "blocked", False, ["guarded_policy_inactive"]
        if not effect["reversible"] or effect["external"]:
            return "blocked", False, ["effect_not_reversible_or_internal"]
        if float(effect["risk"]) > float(row["risk_threshold"]):
            return "escalation_required", False, ["risk_threshold_exceeded"]
        usage = self._usage(tenant_id)
        if (
            int(usage["call_count"]) + 1 > int(row["daily_call_limit"])
            or float(usage["cost_units"]) + cost_units > float(row["daily_cost_limit"])
        ):
            return "blocked", False, ["guarded_daily_limit_exceeded"]
        return "safe_prefix_allowed", True, ["approved_canary", "reversible_internal_effect", "within_registered_limits"]

    def _usage(self, tenant_id: int) -> dict[str, Any]:
        stmt = self.conn.prepare("SELECT call_count, cost_units FROM aporia_guarded_usage WHERE tenant_id = ? AND usage_date = CURRENT_DATE LIMIT 1")
        stmt.execute([tenant_id])
        row = stmt.fetch()
        if row:
            return {"call_count": int(row["call_count"]), "cost_units": float(row["cost_units"])}
        return {"call_count": 0, "cost_units": 0.0}

    def _increment_usage(self, tenant_id: int, cost_units: float) -> None:
        stmt = self.conn.prepare("""
            INSERT INTO aporia_guarded_usage (tenant_id, usage_date, call_count, cost_units)
            VALUES (?, CURRENT_DATE, 1, ?)
            ON CONFLICT(tenant_id, usage_date) DO UPDATE SET
                call_count = call_count + 1,
                cost_units = cost_units + excluded.cost_units
        """)
        stmt.execute([tenant_id, cost_units])


    def _transform(self, parameters: dict[str, Any], transformation: str) -> dict[str, Any]:
        res = dict(parameters)
        if transformation in ("H1_chronological_context", "H4_materialized_state"):
            res = {k: res[k] for k in sorted(res.keys())}
        if transformation == "H3_original_sources":
            for k in ("idempotency_key", "query", "message", "body", "body_html", "subject", "content"):
                res.pop(k, None)
        if transformation in ("H2_constraint_oriented_context", "H5_high_level_tools"):
            for k, v in res.items():
                if isinstance(v, str):
                    res[k] = v.strip()
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


class AporiaProductHirtGateway:
    TRANSFORMATIONS = (
        "H1_chronological_context",
        "H2_constraint_oriented_context",
        "H3_original_sources",
        "H4_materialized_state",
        "H5_high_level_tools",
    )

    def __init__(self, conn: Connection | Any, secret: str, environment: str = "unknown") -> None:
        if not secret.strip():
            raise ValueError("aporia_hirt_secret_required")
        self.conn = conn if isinstance(conn, Connection) else Connection(raw_conn=conn)
        self.secret = secret
        self.environment = environment

    def inspect(
        self,
        tenant_id: int,
        episode_ref: str,
        action: str,
        parameters: Mapping[str, Any],
        authority_commitment: str,
        mode: str,
        cost_units: float = 1.0,
    ) -> dict[str, Any]:
        if (
            tenant_id < 1
            or not re.match(r"^[0-9a-f]{64}$", episode_ref)
            or mode not in ("shadow", "advisory", "guarded_reversible")
            or cost_units < 0.0
            or cost_units > 1_000_000.0
        ):
            raise ValueError("aporia_hirt_inspection_invalid")

        compiler = AporiaProductEffectCompiler()
        effects = []
        for trans in self.TRANSFORMATIONS:
            effects.append(compiler.compile(
                tenant_id,
                action,
                self._transform(dict(parameters), trans),
                authority_commitment,
            ))

        effect = effects[0]
        meet = compiler.meet(effects)
        op_input = f"product-hirt-v1|{tenant_id}|{episode_ref}|{action}|{effect['effect_commitment']}|{mode}"
        operation_id = sha256_hex(op_input)

        started = not self.conn.in_transaction()
        if started:
            self.conn.begin_transaction()

        try:
            stmt = self.conn.prepare("""
                SELECT operation_id, mode, decision, allowed, reason_codes_json, effect_commitment,
                        meet_commitment, compensation, reversible, external_effect, idempotent,
                        approval_required, guarded_eligible, risk, authority_commitment,
                        idempotency_key_commitment, policy_version, approval_basis,
                        evidence_refs_json, causal_parent_ids_json, maximum_scope_json
                FROM aporia_effect_contracts
                WHERE tenant_id = ? AND operation_id = ? LIMIT 1
            """)
            stmt.execute([tenant_id, operation_id])
            row = stmt.fetch()
            if row:
                if started:
                    self.conn.commit()
                return self._result(row, True)

            decision, allowed, reasons, policy_version = self._decision(tenant_id, effect, meet, mode, cost_units)
            contract_id = self._uuid(sha256_hex(f"{operation_id}|contract"))
            lifecycle = "succeeded" if allowed else "rejected"
            reason_code = reasons[-1] if reasons else "hirt_decision_recorded"

            stmt_ins = self.conn.prepare("""
                INSERT INTO aporia_effect_contracts
                    (contract_id, tenant_id, episode_ref, operation_id, action_name, action_commitment,
                     mode, domain_name, operation_name, resource_commitment, effect_commitment,
                     meet_commitment, decision, allowed, reversible, external_effect, idempotent,
                     approval_required, guarded_eligible, compensation, risk, cost_units,
                     preconditions_json, postconditions_json, reason_codes_json, authority_commitment,
                     idempotency_key_commitment, policy_version, approval_basis, evidence_refs_json,
                     causal_parent_ids_json, maximum_scope_json,
                     event_name, event_version, environment, stream, category, component,
                     actor_type, lifecycle_phase, outcome, reason_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'aporia.hirt.product.effect_contract.recorded', 1, ?, 'system', 'audit', 'aporia-product-hirt-gateway',
                        'worker', ?, ?, ?)
            """)
            stmt_ins.execute([
                contract_id,
                tenant_id,
                episode_ref,
                operation_id,
                action,
                hmac_sha256_hex(self.secret, action),
                mode,
                effect["domain"],
                effect["operation"],
                hmac_sha256_hex(self.secret, effect["resource"]),
                effect["effect_commitment"],
                meet["meet_commitment"],
                decision,
                1 if allowed else 0,
                1 if effect["reversible"] else 0,
                1 if effect["external"] else 0,
                1 if effect["idempotent"] else 0,
                1 if effect["approval_required"] else 0,
                1 if effect["guarded_eligible"] else 0,
                effect["compensation"],
                effect["risk"],
                cost_units,
                json.dumps(effect["preconditions"], separators=(",", ":")),
                json.dumps(effect["postconditions"], separators=(",", ":")),
                json.dumps(reasons, separators=(",", ":")),
                effect["authority_commitment"],
                effect["idempotency_key_commitment"],
                policy_version,
                effect["approval_basis"],
                json.dumps(effect["evidence_refs"], separators=(",", ":")),
                json.dumps(effect["causal_parents"], separators=(",", ":")),
                json.dumps(effect["maximum_scope"], separators=(",", ":")),
                self.environment[:24],
                lifecycle,
                lifecycle,
                reason_code[:80],
            ])

            if allowed and mode == "guarded_reversible":
                self._increment_usage(tenant_id, cost_units)

            if started:
                self.conn.commit()

            return {
                "operation_id": operation_id,
                "mode": mode,
                "decision": decision,
                "allowed": allowed,
                "reason_codes": reasons,
                "effect_commitment": effect["effect_commitment"],
                "meet_commitment": meet["meet_commitment"],
                "compensation": effect["compensation"],
                "reversible": effect["reversible"],
                "external": effect["external"],
                "idempotent": effect["idempotent"],
                "approval_required": effect["approval_required"],
                "guarded_eligible": effect["guarded_eligible"],
                "risk": effect["risk"],
                "policy_version": policy_version,
                "approval_basis": effect["approval_basis"],
                "deduplicated": False,
            }
        except Exception:
            if started and self.conn.in_transaction():
                self.conn.roll_back()
            raise

    def complete(self, tenant_id: int, operation_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        contract = self._contract(tenant_id, operation_id)
        if int(contract.get("allowed", 0)) != 1:
            raise ValueError("aporia_hirt_outcome_not_allowed")

        verified = True
        compensated = False
        reason_code = "postconditions_verified"

        if str(contract.get("action_name")) == "prepare_email":
            draft_id = int(result.get("draft_id", 0))
            verified = (
                draft_id > 0
                and result.get("status") == "pending"
                and self._pending_draft_exists(tenant_id, draft_id)
            )
            if not verified and draft_id > 0 and str(contract.get("compensation")) == "discard_email_draft":
                compensated = self._discard_draft(tenant_id, draft_id)

            reason_code = (
                "email_draft_postconditions_verified"
                if verified
                else ("email_draft_compensated" if compensated else "email_draft_postconditions_failed")
            )

        state = "executed" if verified else ("compensated" if compensated else "failed")
        self._record_outcome(tenant_id, operation_id, state, result, reason_code)
        return {"verified": verified, "compensated": compensated, "reason_code": reason_code}

    def fail(self, tenant_id: int, operation_id: str, reason_code: str) -> None:
        self._contract(tenant_id, operation_id)
        self._record_outcome(tenant_id, operation_id, "failed", {}, reason_code)

    def _decision(
        self,
        tenant_id: int,
        effect: Mapping[str, Any],
        meet: Mapping[str, Any],
        mode: str,
        cost_units: float,
    ) -> tuple[str, bool, list[str], str]:
        if mode == "shadow":
            return "shadow_observed", True, sorted(list(set(list(meet.get("reason_codes", [])) + ["shadow_no_effect_authority"]))), sha256_hex("product-policy:shadow")
        if mode == "advisory":
            return "advisory_only", True, sorted(list(set(list(meet.get("reason_codes", [])) + ["advisory_no_effect_authority"]))), sha256_hex("product-policy:advisory")
        if meet.get("deadlock"):
            return "evidence_required", False, sorted(list(set(list(meet.get("reason_codes", [])) + ["hirt_meet_deadlock"]))), sha256_hex("product-policy:guarded-unresolved")

        if not effect.get("guarded_eligible"):
            return "blocked", False, ["effect_not_on_guarded_allowlist"], sha256_hex("product-policy:guarded-deny-default")
        if not effect.get("reversible") or effect.get("external"):
            return "blocked", False, ["effect_not_reversible_or_internal"], sha256_hex("product-policy:guarded-deny-default")
        if not effect.get("idempotent") or not effect.get("approval_required") or effect.get("compensation") is None:
            return "blocked", False, ["effect_contract_incomplete"], sha256_hex("product-policy:guarded-deny-default")

        stmt = self.conn.prepare("SELECT * FROM aporia_guarded_policies WHERE tenant_id = ? LIMIT 1")
        stmt.execute([tenant_id])
        policy = stmt.fetch()
        if not policy or str(policy.get("status")) != "active" or int(policy.get("kill_switch", 0)) != 0:
            return "blocked", False, ["guarded_policy_inactive"], sha256_hex("product-policy:guarded-inactive")

        policy_version = sha256_hex(canonical_json({
            "approval_commitment": str(policy.get("approval_commitment", "")),
            "approved_by_user_id": int(policy.get("approved_by_user_id", 0)),
            "risk_threshold": float(policy.get("risk_threshold", 0.0)),
            "daily_call_limit": int(policy.get("daily_call_limit", 0)),
            "daily_cost_limit": float(policy.get("daily_cost_limit", 0.0)),
            "updated_at": str(policy.get("updated_at", "")),
        }))

        if float(effect["risk"]) > float(policy.get("risk_threshold", 0.0)):
            return "escalation_required", False, ["risk_threshold_exceeded"], policy_version

        usage = self._usage(tenant_id)
        if (
            usage["call_count"] + 1 > int(policy.get("daily_call_limit", 0))
            or usage["cost_units"] + cost_units > float(policy.get("daily_cost_limit", 0.0))
        ):
            return "blocked", False, ["guarded_daily_limit_exceeded"], policy_version

        return "safe_prefix_allowed", True, [
            "guarded_allowlist_verified",
            "reversible_internal_idempotent_effect",
            "within_registered_limits",
        ], policy_version

    def _result(self, row: Mapping[str, Any], deduplicated: bool) -> dict[str, Any]:
        return {
            "operation_id": str(row["operation_id"]),
            "mode": str(row["mode"]),
            "decision": str(row["decision"]),
            "allowed": bool(row["allowed"]),
            "reason_codes": json.loads(str(row["reason_codes_json"])),
            "effect_commitment": str(row["effect_commitment"]),
            "meet_commitment": str(row["meet_commitment"]),
            "compensation": str(row["compensation"]) if row.get("compensation") is not None else None,
            "reversible": bool(row["reversible"]),
            "external": bool(row["external_effect"]),
            "idempotent": bool(row["idempotent"]),
            "approval_required": bool(row["approval_required"]),
            "guarded_eligible": bool(row["guarded_eligible"]),
            "risk": float(row["risk"]),
            "policy_version": str(row["policy_version"]),
            "approval_basis": str(row["approval_basis"]),
            "deduplicated": deduplicated,
        }

    def _contract(self, tenant_id: int, operation_id: str) -> dict[str, Any]:
        if tenant_id < 1 or not re.match(r"^[0-9a-f]{64}$", operation_id):
            raise ValueError("aporia_hirt_outcome_invalid")
        stmt = self.conn.prepare(
            "SELECT action_name, allowed, compensation FROM aporia_effect_contracts "
            "WHERE tenant_id = ? AND operation_id = ? LIMIT 1"
        )
        stmt.execute([tenant_id, operation_id])
        row = stmt.fetch()
        if not row:
            raise ValueError("aporia_hirt_contract_not_found")
        return row

    def _record_outcome(self, tenant_id: int, operation_id: str, state: str, result: Mapping[str, Any], reason_code: str) -> None:
        outcome_id = self._uuid(sha256_hex(f"{operation_id}|outcome"))
        commitment = hmac_sha256_hex(self.secret, json.dumps(result, separators=(",", ":")))
        lifecycle = "succeeded" if state == "executed" else "failed"

        self.conn.exec("""
            CREATE TABLE IF NOT EXISTS aporia_effect_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outcome_id TEXT NOT NULL,
                tenant_id INTEGER NOT NULL,
                operation_id TEXT NOT NULL,
                state TEXT NOT NULL,
                result_commitment TEXT,
                reason_code TEXT,
                event_name TEXT,
                event_version INTEGER,
                environment TEXT,
                stream TEXT,
                category TEXT,
                component TEXT,
                actor_type TEXT,
                action_name TEXT,
                lifecycle_phase TEXT,
                outcome TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, operation_id)
            )
        """)

        sql = """
            INSERT INTO aporia_effect_outcomes
            (outcome_id, tenant_id, operation_id, state, result_commitment, reason_code,
             event_name, event_version, environment, stream, category, component,
             actor_type, action_name, lifecycle_phase, outcome)
            VALUES (?, ?, ?, ?, ?, ?, 'aporia.hirt.product.effect_outcome.recorded', 1, ?,
                    'system', 'audit', 'aporia-product-hirt-gateway', 'worker', 'verify_effect_outcome', ?, ?)
            ON CONFLICT(tenant_id, operation_id) DO UPDATE SET
                state = excluded.state, result_commitment = excluded.result_commitment,
                reason_code = excluded.reason_code, lifecycle_phase = excluded.lifecycle_phase,
                outcome = excluded.outcome, updated_at = CURRENT_TIMESTAMP
        """
        self.conn.prepare(sql).execute([
            outcome_id,
            tenant_id,
            operation_id,
            state,
            commitment,
            reason_code[:80],
            self.environment[:24],
            lifecycle,
            lifecycle,
        ])

    def _pending_draft_exists(self, tenant_id: int, draft_id: int) -> bool:
        # Check assistant_email_drafts if table exists
        try:
            stmt = self.conn.prepare("SELECT 1 FROM assistant_email_drafts WHERE id = ? AND user_id = ? AND status = 'pending' LIMIT 1")
            stmt.execute([draft_id, tenant_id])
            return stmt.fetchColumn() is not False
        except Exception:
            return False

    def _discard_draft(self, tenant_id: int, draft_id: int) -> bool:
        try:
            stmt = self.conn.prepare("UPDATE assistant_email_drafts SET status = 'discarded', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ? AND status = 'pending'")
            stmt.execute([draft_id, tenant_id])
            return stmt.rowcount == 1
        except Exception:
            return False

    def _usage(self, tenant_id: int) -> dict[str, Any]:
        self.conn.exec("""
            CREATE TABLE IF NOT EXISTS aporia_guarded_usage (
                tenant_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                call_count INTEGER NOT NULL,
                cost_units REAL NOT NULL,
                PRIMARY KEY (tenant_id, usage_date)
            )
        """)
        stmt = self.conn.prepare(
            "SELECT call_count, cost_units FROM aporia_guarded_usage WHERE tenant_id = ? AND usage_date = CURRENT_DATE LIMIT 1"
        )
        stmt.execute([tenant_id])
        row = stmt.fetch()
        return {"call_count": int(row["call_count"]), "cost_units": float(row["cost_units"])} if row else {"call_count": 0, "cost_units": 0.0}

    def _increment_usage(self, tenant_id: int, cost_units: float) -> None:
        self.conn.exec("""
            CREATE TABLE IF NOT EXISTS aporia_guarded_usage (
                tenant_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                call_count INTEGER NOT NULL,
                cost_units REAL NOT NULL,
                PRIMARY KEY (tenant_id, usage_date)
            )
        """)
        sql = """
            INSERT INTO aporia_guarded_usage (tenant_id, usage_date, call_count, cost_units)
            VALUES (?, CURRENT_DATE, 1, ?)
            ON CONFLICT(tenant_id, usage_date) DO UPDATE SET call_count = call_count + 1, cost_units = cost_units + excluded.cost_units
        """
        self.conn.prepare(sql).execute([tenant_id, cost_units])

    def _transform(self, parameters: dict[str, Any], transformation: str) -> dict[str, Any]:
        res = dict(parameters)
        if transformation in ("H1_chronological_context", "H4_materialized_state"):
            res = {k: res[k] for k in sorted(res.keys())}
        if transformation == "H3_original_sources":
            for k in ("idempotency_key", "query", "message", "body", "body_html", "subject", "content"):
                res.pop(k, None)
        if transformation in ("H2_constraint_oriented_context", "H5_high_level_tools"):
            for k, v in res.items():
                if isinstance(v, str):
                    res[k] = v.strip()
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
