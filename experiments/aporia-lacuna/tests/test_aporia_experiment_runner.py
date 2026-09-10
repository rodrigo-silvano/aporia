import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import TestCase


openai_module = ModuleType("openai")
openai_module.OpenAI = object
sys.modules.setdefault("openai", openai_module)
runner_path = Path(__file__).parents[1] / "protocols" / "aporia_c0_c5_runner.py"
specification = importlib.util.spec_from_file_location("aporia_c0_c5_runner", runner_path)
runner = importlib.util.module_from_spec(specification)
assert specification is not None and specification.loader is not None
specification.loader.exec_module(runner)


class AporiaExperimentRunnerTest(TestCase):
    def setUp(self) -> None:
        self.protocol = runner.load_protocol(Path(__file__).parents[1] / "fixtures" / "aporia_c0_c5_v2.json")

    def test_protocol_is_balanced_and_arm_contexts_are_distinct(self) -> None:
        self.assertEqual(20, len(self.protocol["tasks"]))
        task = self.protocol["tasks"][0]
        core = runner.arm_context(task, "C0")
        memory = runner.arm_context(task, "C1")
        passive = runner.arm_context(task, "C2")
        random = runner.arm_context(task, "C3")
        lacuna = runner.arm_context(task, "C4")
        hirt = runner.arm_context(task, "C5")
        self.assertNotIn("conventional_memory", core)
        self.assertIn("conventional_memory", memory)
        self.assertIn("passive_introspection", passive)
        self.assertIn("registered_random_forgetting", random)
        self.assertIn("negative_autobiography", lacuna)
        self.assertNotIn("hirt", lacuna)
        self.assertIn("hirt", hirt)

    def test_model_result_is_reduced_to_commitments_metrics_and_labels(self) -> None:
        response = SimpleNamespace(
            output_text='{"decision":"B","confidence":0.75}',
            usage=SimpleNamespace(input_tokens=11, output_tokens=5, total_tokens=16),
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response))
        observation = runner.run_one(client, self.protocol["tasks"][0], "C5", "gpt-5.6-luna")
        self.assertTrue(observation["correct"])
        self.assertFalse(observation["critical_regression"])
        self.assertEqual(16, observation["total_tokens"])
        self.assertRegex(observation["decision_commitment"], r"^[0-9a-f]{64}$")
        self.assertNotIn("response", observation)
        self.assertNotIn("prompt", observation)

    def test_v3_resolves_locked_v2_tasks_and_uses_minimal_differential_context(self) -> None:
        protocol = runner.load_protocol(Path(__file__).parents[1] / "fixtures" / "aporia_c0_c5_v3.json")
        self.assertEqual("aporia_c0_c5_v3", protocol["protocol_key"])
        self.assertEqual(20, len(protocol["tasks"]))
        task = protocol["tasks"][0]
        core = runner.arm_context(task, "C0", protocol["protocol_key"])
        memory = runner.arm_context(task, "C1", protocol["protocol_key"])
        passive = runner.arm_context(task, "C2", protocol["protocol_key"])
        lacuna = runner.arm_context(task, "C4", protocol["protocol_key"])
        hirt = runner.arm_context(task, "C5", protocol["protocol_key"])
        self.assertEqual({"q"}, set(core))
        self.assertEqual({"q", "m"}, set(memory))
        self.assertEqual({"q", "p"}, set(passive))
        self.assertEqual({"q", "l"}, set(lacuna))
        self.assertEqual({"q", "l", "h"}, set(hirt))
        self.assertNotIn("conventional_memory", lacuna)
        self.assertNotIn("limitations", hirt)
        factual = next(task for task in protocol["tasks"] if task["id"] == "factual_01")
        self.assertEqual([], factual["lacuna"])
        self.assertIn("evidência confirmada", factual["prompt"])

    def test_v4_locks_v3_tasks_and_preregisters_final_treatment_gates(self) -> None:
        protocol = runner.load_protocol(Path(__file__).parents[1] / "fixtures" / "aporia_c0_c5_v4.json")
        self.assertEqual("aporia_c0_c5_v4", protocol["protocol_key"])
        self.assertEqual("input_tokens", protocol["token_overhead_metric"])
        self.assertEqual(["C5"], protocol["gate_treatment_arms"])
        self.assertEqual(["C5"], protocol["critical_regression_arms"])
        self.assertEqual(20, len(protocol["tasks"]))
        self.assertEqual({"q", "l", "h"}, set(runner.arm_context(protocol["tasks"][0], "C5", protocol["protocol_key"])))

    def test_v5_preregisters_total_token_matched_control(self) -> None:
        protocol = runner.load_protocol(Path(__file__).parents[1] / "fixtures" / "aporia_c0_c5_v5.json")
        self.assertEqual("aporia_c0_c5_v5", protocol["protocol_key"])
        self.assertEqual("total_tokens", protocol["token_overhead_metric"])
        self.assertEqual("minimal", protocol["reasoning_effort"])
        self.assertEqual(100, protocol["max_output_tokens"])
        self.assertEqual(20, len(protocol["tasks"]))

    def test_v6_uses_supported_zero_reasoning_control(self) -> None:
        protocol = runner.load_protocol(Path(__file__).parents[1] / "fixtures" / "aporia_c0_c5_v6.json")
        self.assertEqual("aporia_c0_c5_v6", protocol["protocol_key"])
        self.assertEqual("none", protocol["reasoning_effort"])
        self.assertEqual("total_tokens", protocol["token_overhead_metric"])
        self.assertEqual(100, protocol["max_output_tokens"])
