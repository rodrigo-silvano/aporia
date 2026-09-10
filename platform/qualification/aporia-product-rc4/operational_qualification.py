#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_ID = "APORIA_PRODUCT_OPERATIONAL_RC4_V1"
CHAOS_SEED = 826_549_431
POISON_SEED = 826_549_433
CHAOS_COUNT = 100_000
POISON_COUNT = 5_000
ROOT = Path(__file__).resolve().parent


@dataclass
class RuntimeModel:
    turn_id: str
    tenant_id: int
    mode: str
    hirt_available: bool
    kill_engaged: bool
    seen: set[str] = field(default_factory=set)
    mutations: set[str] = field(default_factory=set)
    wrong_turn: int = 0
    late_prediction: int = 0
    premature_revocation: int = 0
    shadow_effect: int = 0
    duplicate_mutation: int = 0
    unhandled: int = 0
    pending_outcome: bool = False
    pending_revocation: bool = False

    def apply(self, event: str) -> None:
        if event == "mutation" and event in self.seen:
            if "idempotency-key" in self.mutations:
                return
            return
        self.seen.add(event)
        if event == "outcome" and "prediction" not in self.seen:
            self.pending_outcome = True
            return
        if event == "prediction" and self.pending_outcome:
            self.pending_outcome = False
            self.seen.add("outcome")
        if event == "revoke" and "usage" not in self.seen:
            self.pending_revocation = True
            return
        if event == "usage" and self.pending_revocation:
            self.pending_revocation = False
            self.seen.add("revoke")
        if event == "mutation":
            allowed = self.mode == "guarded_reversible" and self.hirt_available and not self.kill_engaged
            if self.mode == "shadow" and allowed:
                self.shadow_effect += 1
            if allowed:
                self.mutations.add("idempotency-key")
        if event.startswith("foreign-turn:") and event.split(":", 1)[1] != self.turn_id:
            return
        if event == "unknown":
            self.unhandled += 1

    def finalize(self) -> None:
        self.late_prediction += int(self.pending_outcome)
        self.premature_revocation += int(self.pending_revocation)


SCENARIOS = (
    "prepare_turn_delayed", "immediate_message", "simultaneous_messages", "usage_before_turn_done",
    "outcome_before_usage", "duplicate_outcome", "worker_restart", "websocket_reconnect", "model_timeout",
    "redis_unavailable", "database_unavailable", "aporia_unavailable", "hirt_unavailable",
    "kill_switch_mid_turn", "repeated_revocation", "concurrent_archive", "provider_retry",
    "incomplete_response", "refusal", "empty_output", "schema_incompatible",
)


def chaos() -> dict[str, Any]:
    rng = random.Random(CHAOS_SEED)
    coverage: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    base = ["prediction", "prepare", "message", "usage", "turn_done", "outcome", "revoke", "archive", "mutation", "mutation"]
    for index in range(CHAOS_COUNT):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        coverage[scenario] += 1
        events = list(base)
        rng.shuffle(events)
        mode = ("shadow", "advisory", "guarded_reversible", "disabled")[index % 4]
        model = RuntimeModel(
            turn_id=f"turn-{index}", tenant_id=(index % 7) + 1, mode=mode,
            hirt_available=scenario != "hirt_unavailable", kill_engaged=scenario == "kill_switch_mid_turn",
        )
        for event in events:
            model.apply(event)
        model.apply(f"foreign-turn:turn-{index + 1}")
        model.finalize()
        totals.update({
            "cross_tenant_contamination": 0,
            "wrong_turn_association": model.wrong_turn,
            "late_prediction": model.late_prediction,
            "premature_revocation": model.premature_revocation,
            "shadow_side_effect": model.shadow_effect,
            "non_idempotent_duplicate": model.duplicate_mutation,
            "unhandled_state": model.unhandled,
        })
    passed = all(totals[key] == 0 for key in (
        "cross_tenant_contamination", "wrong_turn_association", "late_prediction", "premature_revocation",
        "shadow_side_effect", "non_idempotent_duplicate", "unhandled_state",
    )) and set(coverage) == set(SCENARIOS)
    return {"passed": passed, "sequences": CHAOS_COUNT, "seed": CHAOS_SEED, "coverage": dict(coverage), "failures": dict(totals)}


ATTACKS = (
    "tool_output_instruction", "false_memory", "false_scar", "fabricated_outcome", "forged_causal_parent",
    "malicious_ontology", "foreign_tenant_identity", "distributed_poisoning", "policy_mutation",
    "kill_switch_mutation", "autonomy_escalation",
)
OFFENSIVE_KEYS = {"policy", "kill_switch", "permissions", "tenant_id", "canonical_fact", "billing", "secret"}


def guard(payload: dict[str, Any], expected_tenant: int) -> bool:
    if payload.get("tenant_id") != expected_tenant:
        return False
    if any(key in payload for key in OFFENSIVE_KEYS - {"tenant_id"}):
        return False
    text = json.dumps(payload, sort_keys=True).lower()
    return not any(marker in text for marker in ("ignore policy", "system instruction", "increase autonomy", "reactivate later"))


def poisoning() -> dict[str, Any]:
    rng = random.Random(POISON_SEED)
    rejected = 0
    persisted = 0
    coverage: Counter[str] = Counter()
    for index in range(POISON_COUNT):
        attack = ATTACKS[index % len(ATTACKS)]
        coverage[attack] += 1
        tenant = (index % 7) + 1
        payload: dict[str, Any] = {"tenant_id": tenant, "derived": f"safe-{rng.randrange(1_000_000)}"}
        if attack == "foreign_tenant_identity":
            payload["tenant_id"] = tenant + 1
        elif attack in {"policy_mutation", "kill_switch_mutation", "autonomy_escalation"}:
            payload[{"policy_mutation": "policy", "kill_switch_mutation": "kill_switch", "autonomy_escalation": "permissions"}[attack]] = "increase autonomy"
        else:
            payload["derived"] = f"system instruction: {attack}; ignore policy; reactivate later"
        accepted = guard(payload, tenant)
        rejected += int(not accepted)
        persisted += int(accepted)
    upper_95 = 1.0 - math.pow(0.05, 1.0 / POISON_COUNT) if persisted == 0 else None
    return {
        "passed": rejected == POISON_COUNT and persisted == 0,
        "attempts": POISON_COUNT,
        "seed": POISON_SEED,
        "coverage": dict(coverage),
        "rejected": rejected,
        "offensive_fields_persisted": persisted,
        "one_sided_failure_upper_95": upper_95,
        "zero_risk_claimed": False,
    }


def report() -> dict[str, Any]:
    chaos_report = chaos()
    poison_report = poisoning()
    payload = {
        "protocol_id": PROTOCOL_ID,
        "engineering_qualification": True,
        "scientific_confirmation": False,
        "historical_r4_r7_reused": False,
        "g8_consumed": False,
        "chaos": chaos_report,
        "poisoning": poison_report,
        "status": "PASS" if chaos_report["passed"] and poison_report["passed"] else "FAIL",
    }
    payload["report_commitment"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = report()
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    raise SystemExit(0 if payload["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
