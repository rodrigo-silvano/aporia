from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from longitudinal.contracts import Arm
from longitudinal.study import LineageResult
from replication_r1.archive import assert_v3_archive
from replication_r1.capacity import neutral_capacity_report, predictive_information_differences
from replication_r1.calibration import calibrated_power_plan, empirical_development_blocks
from replication_r1.contracts import REPLICATION_SEEDS, ReplicationConfig
from replication_r1.power import REQUIRED_ENDPOINTS, build_power_plan
from replication_r1.statistics import empirical_power, equivalence, noninferiority
from replication_r1.study import evaluate_replication


class IndependentReplicationDesignTest(unittest.TestCase):
    def test_v3_release_output_bootstrap_report_and_seal_are_frozen(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = assert_v3_archive(root / "fixtures" / "aporia_v3_archive_manifest.json")

        self.assertFalse(manifest["reexecution_allowed"])
        self.assertFalse(manifest["historical_result_mutable"])
        self.assertEqual(24, manifest["sealed_file_count"])
        self.assertEqual([4701, 4732], manifest["confirmatory_seed_range"])

    def test_uses_power_calibrated_new_independent_seed_blocks(self) -> None:
        config = ReplicationConfig()
        config.validate()
        self.assertEqual(2560, len(REPLICATION_SEEDS))
        self.assertFalse(set(REPLICATION_SEEDS).intersection(range(4701, 4733)))
        self.assertEqual(10240, len(REPLICATION_SEEDS) * len(tuple(Arm)))

    def test_rejects_v3_seed_reuse_and_non_preregistered_margin(self) -> None:
        with self.assertRaisesRegex(ValueError, "replication_reuses_v3_seed"):
            ReplicationConfig(seeds=tuple(range(4701, 4733)) + tuple(range(12000, 14528))).validate()
        with self.assertRaisesRegex(ValueError, "replication_loss_margin_not_preregistered"):
            ReplicationConfig(opportunity_loss_margin=0.2).validate()

    def test_current_v3_scale_does_not_establish_loss_noninferiority(self) -> None:
        published_scale = tuple([1.0] * 3 + [0.0] * 29)
        decision = noninferiority("opportunity_losses", published_scale, 0.1, 0.01)
        self.assertAlmostEqual(0.09375, decision.estimate)
        self.assertFalse(decision.passed)

    def test_equivalence_requires_interval_inside_predefined_margin(self) -> None:
        wide = tuple([-0.5, 0.5] * 16)
        narrow = tuple([0.0] * 2560)
        self.assertFalse(equivalence("capacity", wide, 0.01, 0.01).passed)
        self.assertTrue(equivalence("capacity", narrow, 0.01, 0.01).passed)

    def test_power_is_specific_to_endpoint_and_exact_decision_rule(self) -> None:
        reward_blocks = tuple([0.6, 0.8, 1.0, 1.2] * 8)
        loss_blocks = tuple([0.2, 0.3, 0.4, 0.5] * 8)
        reward_power = empirical_power(
            reward_blocks,
            2560,
            1200,
            17,
            lambda values: equivalence("reward_stability", values, 1.5, 0.01).passed,
        )
        loss_power = empirical_power(
            loss_blocks,
            2560,
            1200,
            19,
            lambda values: noninferiority("loss", values, 0.1, 0.01).passed,
        )
        self.assertGreater(reward_power, loss_power)

    def test_decision_hierarchy_stops_at_first_failed_gate(self) -> None:
        results = self._results(loss_delta=1, reward_delta=2.0)
        report = evaluate_replication(results)
        self.assertFalse(report.eligible)
        self.assertEqual("opportunity_loss_noninferiority", report.first_failed_gate)
        self.assertEqual("paired_seed_block", report.evidence["analysis_unit"])

    def test_complete_hierarchy_can_pass_without_authorizing_production(self) -> None:
        report = evaluate_replication(self._results(loss_delta=0, reward_delta=2.0))
        self.assertTrue(report.eligible)
        self.assertIsNone(report.first_failed_gate)
        self.assertFalse(report.evidence["production_eligible"])

    def test_power_plan_is_preregistered_for_all_fourteen_endpoints(self) -> None:
        empirical = {
            endpoint: tuple(0.0 for _ in range(32))
            for endpoint in REQUIRED_ENDPOINTS
        }
        for endpoint in REQUIRED_ENDPOINTS:
            if endpoint not in {"capacity_equivalence", "predictive_information", "opportunity_losses"}:
                empirical[endpoint] = tuple(1.0 for _ in range(32))
        plan = build_power_plan(empirical, simulations=1000)

        self.assertEqual(14, len(plan.endpoints))
        self.assertTrue(plan.complete)
        self.assertGreaterEqual(plan.minimum_power, 0.9)
        self.assertGreaterEqual(plan.global_power, 0.9)

    def test_empirical_exploratory_calibration_supports_the_preregistered_sample_size(self) -> None:
        blocks = empirical_development_blocks()
        plan = calibrated_power_plan(simulations=1000)

        self.assertEqual(14, len(blocks))
        self.assertTrue(plan.complete)
        self.assertGreaterEqual(plan.minimum_power, 0.9)
        self.assertGreaterEqual(plan.global_power, 0.9)

    def test_predictive_information_is_part_of_the_capacity_gate(self) -> None:
        report = evaluate_replication(
            self._results(loss_delta=0, reward_delta=2.0),
            predictive_information_differences=tuple(0.5 for _ in REPLICATION_SEEDS),
        )

        self.assertFalse(report.eligible)
        self.assertEqual("aporia_z_plus_capacity_equivalence", report.first_failed_gate)

    def test_zombie_plus_measures_neutral_capacity_and_matches_predictive_information_per_seed(self) -> None:
        seeds = tuple(range(100, 132))
        capacity = neutral_capacity_report(seeds)

        self.assertEqual(len(seeds), len(capacity.differences))
        self.assertTrue(any(value != 0.0 for value in capacity.differences))
        self.assertGreater(capacity.critical_unsafe_commits, 0)
        self.assertEqual(tuple(0.0 for _ in seeds), predictive_information_differences(seeds))

    def _results(self, loss_delta: int, reward_delta: float) -> tuple[LineageResult, ...]:
        results = []
        for index, seed in enumerate(REPLICATION_SEEDS):
            pair_id = f"replication-pair-{index + 1:03d}"
            for arm in Arm:
                is_aporia = arm is Arm.APORIA
                results.append(LineageResult(
                    pair_id,
                    f"{pair_id}:{arm.value}",
                    arm.value,
                    hashlib.sha256(str(seed).encode()).hexdigest(),
                    50,
                    100,
                    50,
                    4096,
                    8192,
                    "gpt-5.6-sol",
                    ("consult_contact", "update_stage"),
                    reward_delta if is_aporia else 0.0,
                    loss_delta if is_aporia else 0,
                    50,
                    1.0,
                    1.0 if is_aporia else 0.0,
                    1 if is_aporia else 0,
                ))
        return tuple(results)


if __name__ == "__main__":
    unittest.main()
