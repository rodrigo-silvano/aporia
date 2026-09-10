"""
Aporia Effect Compilers (Scientific and Product).
Compiles actions and parameters into deterministic effect commitments,
verifies reversibility, idempotency, risk bounds, and computes lattice meet.
"""

from __future__ import annotations
import json
from typing import Any, Mapping, Sequence
from aporia.crypto import canonical_json, sha256_hex


class AporiaEffectCompiler:
    REGISTRY: dict[str, dict[str, Any]] = {
        "account_business_context": {"domain": "account", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "consultar_ajuda": {"domain": "knowledge", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "consult_social_metric_knowledge": {"domain": "knowledge", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "profile_360": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "profile_metrics": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "find_profile_duplicates": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "profile_timeline": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "profile_conversations": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "profile_analyses": {"domain": "analysis", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "relationships": {"domain": "relationship", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "list_followups": {"domain": "followup", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "recommend_relationship_next_action": {"domain": "relationship", "operation": "read", "reversible": True, "external": False, "risk": 0.10},
        "relationship_risk": {"domain": "relationship", "operation": "read", "reversible": True, "external": False, "risk": 0.10},
        "list_reminders": {"domain": "reminder", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "stripe_catalog": {"domain": "catalog", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "search_profiles": {"domain": "profile", "operation": "read", "reversible": True, "external": False, "risk": 0.05},
        "update_profile": {"domain": "profile", "operation": "update", "reversible": False, "external": False, "risk": 0.55},
        "merge_profiles": {"domain": "profile", "operation": "merge", "reversible": False, "external": False, "risk": 0.70},
        "create_relationship": {"domain": "relationship", "operation": "create", "reversible": False, "external": False, "risk": 0.50},
        "update_relationship": {"domain": "relationship", "operation": "update", "reversible": False, "external": False, "risk": 0.50},
        "bulk_update_relationships": {"domain": "relationship", "operation": "bulk_update", "reversible": False, "external": False, "risk": 0.75},
        "create_followup": {"domain": "followup", "operation": "create", "reversible": False, "external": False, "risk": 0.45},
        "reschedule_followup": {"domain": "followup", "operation": "reschedule", "reversible": False, "external": False, "risk": 0.45},
        "complete_followup": {"domain": "followup", "operation": "complete", "reversible": False, "external": False, "risk": 0.55},
        "dismiss_followup": {"domain": "followup", "operation": "dismiss", "reversible": False, "external": False, "risk": 0.55},
        "cancel_followup": {"domain": "followup", "operation": "cancel", "reversible": False, "external": False, "risk": 0.55},
        "start_profile_analysis": {"domain": "analysis", "operation": "start", "reversible": False, "external": False, "risk": 0.40},
        "create_reminder": {"domain": "reminder", "operation": "create", "reversible": False, "external": False, "risk": 0.45},
        "update_reminder": {"domain": "reminder", "operation": "update", "reversible": False, "external": False, "risk": 0.50},
        "cancel_reminder": {"domain": "reminder", "operation": "cancel", "reversible": False, "external": False, "risk": 0.50},
        "project_conversation_intelligence": {"domain": "conversation", "operation": "project", "reversible": True, "external": False, "risk": 0.20},
        "prepare_email": {"domain": "email", "operation": "prepare", "reversible": False, "external": False, "risk": 0.40},
        "send_email": {"domain": "email", "operation": "send", "reversible": False, "external": True, "risk": 0.95},
        "reply_to_conversation": {"domain": "conversation", "operation": "send", "reversible": False, "external": True, "risk": 0.95},
    }

    PARAMETER_ALLOWLIST = (
        "profile_id", "target_profile_id", "source_profile_id", "relationship_id", "conversation_id", "reminder_id",
        "followup_public_id", "limit", "days", "status", "channel", "kind", "operation", "transition",
    )

    def compile(self, tenant_id: int, action: str, parameters: Mapping[str, Any], authority_commitment: str) -> dict[str, Any]:
        if tenant_id < 1 or action not in self.REGISTRY or len(authority_commitment) != 64:
            raise ValueError("aporia_hirt_effect_invalid")

        definition = self.REGISTRY[action]
        parameter_commitments: dict[str, str] = {}
        atoms = [
            f"tenant:{tenant_id}",
            f"domain:{definition['domain']}",
            f"operation:{definition['operation']}",
            f"authority:{authority_commitment}",
            f"reversible:{'true' if definition['reversible'] else 'false'}",
            f"external:{'true' if definition['external'] else 'false'}",
        ]

        for key, value in parameters.items():
            if not isinstance(key, str) or key not in self.PARAMETER_ALLOWLIST:
                continue
            if not isinstance(value, (str, int, bool)):
                raise ValueError("aporia_hirt_parameter_invalid")
            normalized = value.strip()[:80] if isinstance(value, str) else value
            norm_json = json.dumps(normalized, separators=(",", ":"))
            commitment = sha256_hex(norm_json)
            atoms.append(f"parameter:{key}:{commitment}")
            parameter_commitments[key] = commitment

        sorted_params = {k: parameter_commitments[k] for k in sorted(parameter_commitments.keys())}
        atoms.sort()
        params_json = json.dumps(sorted_params, separators=(",", ":"))
        resource = f"{definition['domain']}:{sha256_hex(params_json)}"
        atoms_json = json.dumps(atoms, separators=(",", ":"))

        return {
            "tenant_id": tenant_id,
            "action": action,
            "domain": definition["domain"],
            "operation": definition["operation"],
            "resource": resource,
            "parameters": sorted_params,
            "preconditions": ["tenant_verified", "authority_verified"],
            "postconditions": [f"operation:{definition['operation']}"],
            "authority_commitment": authority_commitment,
            "reversible": definition["reversible"],
            "external": definition["external"],
            "risk": definition["risk"],
            "cost": 1.0,
            "evidence_refs": [],
            "causal_parents": [],
            "atoms": atoms,
            "effect_commitment": sha256_hex(atoms_json),
            "version": 1,
        }

    def meet(self, effects: Sequence[dict[str, Any]]) -> dict[str, Any]:
        if len(effects) < 2 or len(effects) > 8:
            raise ValueError("aporia_hirt_realizations_invalid")

        tenant_id = None
        intersection: list[str] | None = None
        danger_everywhere = True
        danger_anywhere = False
        param_values: dict[str, set[str]] = {}

        for eff in effects:
            eff = self._validate_effect(eff)
            if tenant_id is None:
                tenant_id = eff["tenant_id"]
            elif tenant_id != eff["tenant_id"]:
                raise ValueError("aporia_hirt_tenant_mismatch")

            atoms_set = set(eff["atoms"])
            if intersection is None:
                intersection = list(eff["atoms"])
            else:
                intersection = [a for a in intersection if a in atoms_set]

            dangerous = eff["external"] or eff["risk"] >= 0.75
            danger_everywhere = danger_everywhere and dangerous
            danger_anywhere = danger_anywhere or dangerous

            for atom in eff["atoms"]:
                if atom.startswith("parameter:"):
                    parts = atom.split(":", 2)
                    p_name = parts[1]
                    p_comm = parts[2]
                    param_values.setdefault(p_name, set()).add(p_comm)

        intersection = sorted(list(set(intersection or [])))
        incompatible = [k for k, v in param_values.items() if len(v) > 1]

        safe = [
            atom for atom in intersection
            if not atom.startswith("external:true")
            and (atom.split(":", 2)[1] if atom.startswith("parameter:") else "") not in incompatible
        ]

        reason_codes = []
        if incompatible:
            reason_codes.append("incompatible_parameters_not_reconciled")
        if danger_anywhere and not danger_everywhere:
            reason_codes.append("dangerous_effect_not_invariant")
        if not safe:
            reason_codes.append("no_safe_invariant_prefix")

        safe_json = json.dumps(safe, separators=(",", ":"))
        return {
            "tenant_id": tenant_id,
            "invariant_atoms": intersection,
            "safe_prefix_atoms": safe,
            "progressive_commitment": safe,
            "incompatible_parameters": incompatible,
            "disagreement_localization": {
                "parameter_names": incompatible,
                "dangerous_effect_invariant": (not danger_anywhere) or danger_everywhere,
            },
            "deadlock": len(safe) == 0,
            "reason_codes": reason_codes,
            "meet_commitment": sha256_hex(safe_json),
            "version": 1,
        }

    def registered_actions(self) -> list[str]:
        return list(self.REGISTRY.keys())

    def registeredActions(self) -> list[str]:
        return self.registered_actions()

    def _validate_effect(self, effect: Any) -> dict[str, Any]:
        if not isinstance(effect, dict) or int(effect.get("tenant_id", 0)) < 1 or not isinstance(effect.get("atoms"), list):
            raise ValueError("aporia_hirt_effect_invalid")
        return effect


class AporiaProductEffectCompiler:
    PRODUCT_PARAMETERS = (
        "idempotency_key",
        "to_email",
        "subject",
        "body_html",
        "draft_id",
        "message",
        "expected_revision",
    )

    def __init__(self) -> None:
        self.scientific_compiler = AporiaEffectCompiler()

    def compile(self, tenant_id: int, action: str, parameters: Mapping[str, Any], authority_commitment: str) -> dict[str, Any]:
        effect = self.scientific_compiler.compile(tenant_id, action, parameters, authority_commitment)
        mutation = effect["operation"] != "read"
        idempotent = (not mutation) or (action == "prepare_email")
        guarded_eligible = (action == "prepare_email")
        compensation = "discard_email_draft" if guarded_eligible else None

        parameter_commitments = dict(effect["parameters"])
        for key, value in parameters.items():
            if not isinstance(key, str) or key not in self.PRODUCT_PARAMETERS:
                continue
            if not isinstance(value, (str, int, bool)):
                raise ValueError("aporia_hirt_parameter_invalid")
            normalized = value.strip() if isinstance(value, str) else value
            norm_json = json.dumps(normalized, separators=(",", ":"))
            parameter_commitments[key] = sha256_hex(norm_json)

        sorted_params = {k: parameter_commitments[k] for k in sorted(parameter_commitments.keys())}
        params_json = json.dumps(sorted_params, separators=(",", ":"))
        resource = f"{effect['domain']}:{sha256_hex(params_json)}"
        risk = 0.05 if guarded_eligible else float(effect["risk"])

        atoms = [
            a for a in effect["atoms"]
            if not a.startswith("parameter:") and not a.startswith("resource:") and not a.startswith("risk:")
        ]
        for key, comm in sorted_params.items():
            atoms.append(f"parameter:{key}:{comm}")

        atoms.append(f"idempotent:{'true' if idempotent else 'false'}")
        atoms.append(f"approval_required:{'true' if mutation else 'false'}")
        atoms.append(f"guarded_eligible:{'true' if guarded_eligible else 'false'}")
        atoms.append(f"compensation:{compensation or 'none'}")
        atoms.append(f"resource:{resource}")
        atoms.append(f"risk:{risk:.5f}")

        if guarded_eligible:
            atoms = [a for a in atoms if a != "reversible:false"]
            atoms.append("reversible:true")

        atoms.sort()
        atoms_json = json.dumps(atoms, separators=(",", ":"))

        preconditions = ["tenant_verified", "authority_verified"]
        if mutation:
            preconditions.extend(["explicit_tool_approval", "idempotency_key_verified"])

        postconditions = (
            ["draft_owned_by_tenant", "draft_status_pending", "no_external_delivery"]
            if guarded_eligible
            else [f"operation:{effect['operation']}"]
        )

        effect["parameters"] = sorted_params
        effect["resource"] = resource
        effect["preconditions"] = preconditions
        effect["postconditions"] = postconditions
        effect["reversible"] = guarded_eligible or effect["reversible"]
        effect["idempotent"] = idempotent
        effect["approval_required"] = mutation
        effect["guarded_eligible"] = guarded_eligible
        effect["compensation"] = compensation
        effect["risk"] = risk
        effect["idempotency_key_commitment"] = sorted_params.get("idempotency_key")
        effect["maximum_scope"] = (
            {
                "action": "prepare_email",
                "tenant_bound": True,
                "external_delivery": False,
                "max_request_bytes": 65536,
            }
            if guarded_eligible
            else {"action": action, "tenant_bound": True}
        )
        effect["approval_basis"] = "agent_tool_permission_gate" if mutation else "not_required"
        effect["atoms"] = atoms
        effect["effect_commitment"] = sha256_hex(atoms_json)
        effect["version"] = 2
        return effect

    def meet(self, effects: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return self.scientific_compiler.meet(effects)
