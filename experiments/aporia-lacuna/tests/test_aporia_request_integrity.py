from __future__ import annotations

import copy
import json
from pathlib import Path

from hosted.request_integrity import canonical_json, decision_context_hash, evidence, model_request_hash


ROOT = Path(__file__).resolve().parents[3]
VECTOR = ROOT / "experiments" / "aporia-lacuna" / "fixtures" / "aporia_request_hash_vectors_r1.json"


def test_request_hash_vectors_and_semantic_sensitivity() -> None:
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    request = vector["request"]
    context = vector["decision_context"]
    wire = canonical_json(request)
    result = evidence(request, context, wire)
    assert result == vector["expected"]
    equivalent = copy.deepcopy(request)
    equivalent["volatile_trace"] = "different-but-irrelevant"
    assert model_request_hash(equivalent) == result["model_request_sha256"]
    for path, value in (
        (("model",), "gpt-5.6-luna-next"),
        (("reasoning", "effort"), "low"),
        (("previous_response_id",), "resp_changed"),
        (("tools",), list(reversed(request["tools"])) + [{"type": "function", "name": "second"}]),
    ):
        changed = copy.deepcopy(request)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert model_request_hash(changed) != result["model_request_sha256"]
    changed_context = copy.deepcopy(context)
    changed_context["policy_version"] = "guarded-reversible-v2"
    changed_context["request_sha256"] = result["model_request_sha256"]
    baseline_context = {**context, "request_sha256": result["model_request_sha256"]}
    assert decision_context_hash(changed_context) != decision_context_hash(baseline_context)


def test_tool_order_changes_hash_without_exposing_sensitive_content() -> None:
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    request = copy.deepcopy(vector["request"])
    request["tools"].append({"type": "function", "name": "second", "parameters": {"type": "object"}})
    first = model_request_hash(request)
    request["tools"].reverse()
    assert model_request_hash(request) != first
    result = evidence(request, vector["decision_context"], canonical_json(request))
    encoded = json.dumps(result, sort_keys=True)
    assert "Escolhe A ou B" not in encoded
    assert "resp_synthetic_previous" not in encoded
