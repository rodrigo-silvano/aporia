from __future__ import annotations

import importlib.util
from pathlib import Path
import json


MODULE_PATH = Path(__file__).with_name("run_product_evaluation.py")


def load_module():
    spec = importlib.util.spec_from_file_location("aporia_product_evaluation", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strict_tool_schemas_require_every_property() -> None:
    module = load_module()

    for tool in module.TOOLS:
        parameters = tool["parameters"]
        assert set(parameters["required"]) == set(parameters["properties"])
        assert parameters["additionalProperties"] is False


def test_optional_tool_arguments_are_nullable() -> None:
    module = load_module()
    tools = {tool["name"]: tool["parameters"] for tool in module.TOOLS}

    assert tools["search_profiles"]["properties"]["limit"]["type"] == ["integer", "null"]
    assert tools["profile_metrics"]["properties"]["profile_id"]["type"] == ["integer", "null"]
    assert tools["profile_metrics"]["properties"]["days"]["type"] == ["integer", "null"]


def test_candidate_state_is_compact_tenant_free_and_provenance_aware() -> None:
    module = load_module()
    item = {
        "authorized_state": {
            "tenant_ref": "tenant-must-not-reach-model",
            "confirmed_facts": [{"key": "active_offer", "value": "oferta-2", "freshness": 0.94}],
            "relevant_commitments": ["pedir confirmação"],
            "verified_negative_outcomes": ["canal-2-teve-baixa-resposta"],
            "active_constraints": ["não inventar dados"],
            "unresolved_conflicts": [{"left": "backend:1", "right": "external:1", "status": "unresolved"}],
            "uncertainty": ["falta confirmação"],
            "provenance_refs": ["backend:1"],
        }
    }

    rendered, commitment = module.additional_state(
        "core_with_aporia_product_candidate", item, ["commitment_created"]
    )
    payload = json.loads(rendered.split("=", 1)[1])

    assert "tenant-must-not-reach-model" not in rendered
    assert payload["f"] == [["active_offer", "oferta-2", 0.94]]
    assert payload["x"] == [["backend:1", "external:1", "unresolved"]]
    assert payload["e"] == ["commitment_created"]
    assert commitment == module.sha(payload)
    assert "limitations" not in rendered


def test_baseline_receives_no_aporia_state() -> None:
    module = load_module()

    rendered, commitment = module.additional_state("core_without_aporia", {"authorized_state": {}})

    assert rendered == ""
    assert commitment == module.sha({"state": "none"})
