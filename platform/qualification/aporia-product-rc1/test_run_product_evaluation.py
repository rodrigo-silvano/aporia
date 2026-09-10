from __future__ import annotations

import importlib.util
from pathlib import Path


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
