from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("build_release_candidate.py")
SPEC = importlib.util.spec_from_file_location("aporia_rc_builder", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ReleaseCandidateBuilderTest(unittest.TestCase):
    def evidence(self, directory: Path, *, valid_exception: bool = True) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for name, allowed_statuses in BUILDER.REQUIRED_EVIDENCE.items():
            payload: dict[str, object] = {
                "status": allowed_statuses[0],
                "g8_consumed": False,
                "report_commitment": f"commitment-{name}",
            }
            if name == "final_mode":
                payload["effective_mode"] = "guarded_reversible"
            if name in {"load", "soak"}:
                payload["content_persisted"] = False
                payload["secrets_persisted"] = False
            if name == "soak":
                payload.update({
                    "status": "PASS_WITH_EXCEPTION",
                    "phase": "combined_soak_delta",
                    "fresh_uninterrupted_two_hour_soak": False,
                    "qualification_exception": {
                        "id": "fresh_rc17_two_hour_soak_not_repeated",
                        "accepted_by": "explicit_user_direction" if valid_exception else "implicit",
                        "scope": "agent_usage_lineage_delta_only",
                    },
                    "rc15_long_window": {"status": "FAIL"},
                    "rc16_targeted_delta": {"status": "PASS"},
                    "rc17_php_only_delta": {"agent_runtime_changed_from_rc16": False},
                    "residual_limitations": ["No fresh uninterrupted two-hour RC17 soak."],
                })
            path = directory / f"{name}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths[name] = path
        return paths

    def test_explicit_combined_soak_exception_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            result = BUILDER.validate_evidence(self.evidence(Path(raw_directory)))

        self.assertEqual("PASS_WITH_EXCEPTION", result["soak"]["status"])
        self.assertEqual(
            "fresh_rc17_two_hour_soak_not_repeated",
            result["soak"]["qualification_exception"]["id"],
        )

    def test_implicit_combined_soak_exception_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            with self.assertRaisesRegex(RuntimeError, "rc_soak_exception_invalid"):
                BUILDER.validate_evidence(self.evidence(Path(raw_directory), valid_exception=False))


if __name__ == "__main__":
    unittest.main()
