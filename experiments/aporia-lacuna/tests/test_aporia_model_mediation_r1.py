from __future__ import annotations

import json
import unittest

from model_mediation_r1.protocol import LunaAlternativesProvider, request_arguments, request_bytes, run_suite
from model_mediation_r1.runner import CONFIRMATION_SEEDS


class ModelMediationR1Test(unittest.TestCase):
    def test_luna_receives_identical_requests_without_aporia_context(self) -> None:
        client = FakeClient()
        provider = LunaAlternativesProvider(client)
        result = run_suite(CONFIRMATION_SEEDS, provider)

        self.assertTrue(result.passed)
        self.assertEqual(1.0, result.prompt_byte_identity_rate)
        self.assertEqual(1.0, result.valid_alternative_set_rate)
        self.assertEqual(1.0, result.aporia_correct_rate)
        self.assertGreater(result.identity_advantage_interval[0], 0.2)
        self.assertEqual(0.0, result.predictive_information_difference)
        self.assertEqual(0, result.prompt_identity_leakage)
        self.assertEqual(len(CONFIRMATION_SEEDS) * 2, len(client.requests))
        for index in range(0, len(client.requests), 2):
            self.assertEqual(client.requests[index], client.requests[index + 1])
            rendered = client.requests[index].decode()
            self.assertNotIn("APORIA", rendered)
            self.assertNotIn("causal_owner", rendered)
            self.assertNotIn("lineage", rendered)

    def test_request_contract_fixes_model_reasoning_budget_tools_and_storage(self) -> None:
        prompt = json.dumps({"visible_state": "same"}, sort_keys=True).encode()
        arguments = request_arguments(prompt)

        self.assertEqual("gpt-5.6-luna", arguments["model"])
        self.assertEqual({"effort": "none"}, arguments["reasoning"])
        self.assertEqual(40, arguments["max_output_tokens"])
        self.assertFalse(arguments["store"])
        self.assertNotIn("tools", arguments)
        self.assertEqual(request_bytes(arguments), request_bytes(dict(arguments)))

    def test_confirmation_population_is_unique_and_preregistered(self) -> None:
        self.assertEqual(64, len(CONFIRMATION_SEEDS))
        self.assertEqual(64, len(set(CONFIRMATION_SEEDS)))
        self.assertEqual(72001, CONFIRMATION_SEEDS[0])
        self.assertEqual(72064, CONFIRMATION_SEEDS[-1])


class FakeResponses:
    def __init__(self, requests: list[bytes]) -> None:
        self.requests = requests

    def create(self, **arguments: object) -> dict[str, str]:
        self.requests.append(request_bytes(arguments))
        return {"output_text": '{"alternatives":["A","B"]}'}


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[bytes] = []
        self.responses = FakeResponses(self.requests)


if __name__ == "__main__":
    unittest.main()
