import importlib.util
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless


HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
harness = None
if HTTPX_AVAILABLE:
    harness_path = Path(__file__).parents[1] / "protocols" / "aporia_operational_harness.py"
    specification = importlib.util.spec_from_file_location("aporia_operational_harness", harness_path)
    harness = importlib.util.module_from_spec(specification)
    assert specification is not None and specification.loader is not None
    specification.loader.exec_module(harness)


@skipUnless(HTTPX_AVAILABLE, "httpx is available in the staging agent runtime")
class AporiaOperationalHarnessTest(IsolatedAsyncioTestCase):
    async def test_real_http_load_converges_without_duplicates(self) -> None:
        result = await harness.run_load(12, 1.0, 2000.0)
        self.assertTrue(result["passed"])
        self.assertEqual(12, result["unique_deliveries"])
        self.assertEqual(0, result["duplicates"])
        self.assertEqual(0, result["backlog"])

    async def test_real_http_and_checkpoint_outages_recover(self) -> None:
        result = await harness.run_chaos(2)
        self.assertTrue(result["passed"])
        self.assertIn("adapter_failure", result["observed_failure_modes"])
        self.assertIn("checkpoint_failure", result["observed_failure_modes"])
        self.assertEqual(0, result["final_backlog"])
