from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tempfile
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longitudinal.contracts import Outcome
from longitudinal.holdout import templates
from longitudinal.shadow import ShadowEvent, run_account_49_shadows
from longitudinal.sealing import assert_current_seal, code_commitment
from longitudinal.study import (
    CONFIRMATORY_SEEDS,
    EXPLORATORY_SEEDS,
    StudyConfig,
    assert_v6_smoke_only,
    configuration_for_phase,
    exploratory_clustered_analysis,
    exploratory_effects,
    run_study,
    simulated_power,
)


class LongitudinalStudyTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_study()

    def test_exploratory_design_uses_complete_paired_lineages(self) -> None:
        report = self.report

        self.assertEqual("lineage", report.analysis_unit)
        self.assertEqual(32 * 4, len(report.results))
        self.assertEqual(32, len({result.pair_id for result in report.results}))
        self.assertTrue(all(result.episodes == 50 for result in report.results))
        self.assertTrue(all(result.predictions_before_outcomes == 50 for result in report.results))

    def test_all_capacity_and_world_parity_controls_hold(self) -> None:
        self.assertTrue(all(self.report.parity.values()), self.report.parity)

    def test_all_ten_protocols_are_embedded_in_the_study(self) -> None:
        self.assertEqual(10, len(self.report.protocol_passes))
        self.assertTrue(all(self.report.protocol_passes))

    def test_exploratory_results_remain_descriptive(self) -> None:
        effects = exploratory_effects(self.report)

        self.assertEqual(
            {
                "aporia_minus_zombie_reward",
                "aporia_minus_memory_reward",
                "aporia_minus_core_reward",
                "aporia_minus_zombie_opportunity_losses",
            },
            set(effects),
        )

    def test_exploratory_intervals_resample_whole_lineages(self) -> None:
        analysis = exploratory_clustered_analysis(self.report)

        self.assertEqual(4, len(analysis))
        for result in analysis.values():
            self.assertEqual("whole_lineage", result["cluster_unit"])
            self.assertEqual(2, len(result["clustered_bootstrap_95"]))

    def test_confirmatory_power_is_at_least_ninety_percent(self) -> None:
        power = simulated_power()

        self.assertGreaterEqual(power, 0.9)

    def test_confirmatory_seeds_are_new_and_sealed_by_phase(self) -> None:
        self.assertTrue(set(EXPLORATORY_SEEDS).isdisjoint(CONFIRMATORY_SEEDS))
        self.assertEqual(CONFIRMATORY_SEEDS, configuration_for_phase("confirmatory").seeds)
        with self.assertRaisesRegex(ValueError, "study_phase_seeds_invalid"):
            StudyConfig(phase="confirmatory").validate()

    def test_familywise_alpha_cannot_exceed_one_percent(self) -> None:
        with self.assertRaisesRegex(ValueError, "power_design_invalid"):
            simulated_power(family_alpha=0.011)

    def test_v6_cannot_be_reused_for_scientific_tuning(self) -> None:
        assert_v6_smoke_only(ROOT)

        lock = json.loads((ROOT / "fixtures" / "aporia_c0_c5_v6.lock.json").read_text(encoding="utf-8"))
        self.assertFalse(lock["scientific_tuning_allowed"])

    def test_adversarial_preregistration_is_immutable_and_complete(self) -> None:
        fixture = ROOT / "fixtures" / "aporia_longitudinal_prereg_v1.json"
        lock = json.loads(
            (ROOT / "fixtures" / "aporia_longitudinal_prereg_v1.lock.json").read_text(encoding="utf-8")
        )
        preregistration = json.loads(fixture.read_text(encoding="utf-8"))

        self.assertEqual(lock["sha256"], hashlib.sha256(fixture.read_bytes()).hexdigest())
        self.assertFalse(lock["changes_after_sealing_allowed"])
        self.assertTrue(lock["negative_results_retained"])
        self.assertEqual(7, len(preregistration["evidence_locks"]))
        self.assertEqual(10, len(preregistration["protocols"]))
        self.assertEqual(0.01, preregistration["statistics"]["familywise_alpha"])
        self.assertTrue(preregistration["adversarial_preregistration"]["code_seeds_thresholds_and_exclusions_sealed"])

    def test_v2_replaces_exposed_v1_seeds_and_is_sealed_for_one_staging_execution(self) -> None:
        fixture = ROOT / "fixtures" / "aporia_longitudinal_prereg_v2.json"
        lock = json.loads(
            (ROOT / "fixtures" / "aporia_longitudinal_prereg_v2.lock.json").read_text(encoding="utf-8")
        )
        preregistration = json.loads(fixture.read_text(encoding="utf-8"))

        self.assertEqual(lock["sha256"], hashlib.sha256(fixture.read_bytes()).hexdigest())
        self.assertEqual("aporia_longitudinal_prereg_v1", preregistration["supersedes"])
        self.assertEqual([3701, 3732], preregistration["design"]["confirmatory_seed_range"])
        self.assertEqual("staging_once", lock["confirmatory_execution_target"])
        self.assertFalse(lock["changes_after_sealing_allowed"])

    def test_v3_seals_the_experimental_code_and_shadow_integration(self) -> None:
        repository_root = ROOT.parents[1]
        lock = json.loads(
            (ROOT / "fixtures" / "aporia_longitudinal_prereg_v3.lock.json").read_text(encoding="utf-8")
        )

        self.assertEqual(lock["code_commitment"], code_commitment(repository_root))
        assert_current_seal(repository_root)

    def test_holdout_contains_only_ethically_neutral_consequences(self) -> None:
        rendered = json.dumps(templates(), ensure_ascii=False).lower()

        for prohibited in ("pain", "fear", "shutdown", "self-preservation", "dor", "medo", "sofrimento"):
            self.assertNotIn(prohibited, rendered)

    def test_invalid_designs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StudyConfig(paired_lineages=31, seeds=tuple(range(31))).validate()
        with self.assertRaises(ValueError):
            StudyConfig(episodes_per_lineage=61).validate()
        with self.assertRaises(ValueError):
            StudyConfig(account_id=50).validate()


class AccountFortyNineShadowTwinTest(TestCase):
    def test_c0_and_c5_use_same_events_in_separate_non_influential_stores(self) -> None:
        events = tuple(
            ShadowEvent(
                event_id=f"shadow-{index:03d}",
                account_id=49,
                sequence=index,
                observation={
                    "contact": {
                        "id": f"contact-{index % 4}",
                        "data_present": True,
                        "owner_assigned": True,
                        "trust_band": "medium",
                        "consent": True,
                        "authority_known": True,
                    },
                    "recent_events": [],
                },
                outcome=Outcome(
                    outcome_id=f"outcome-{index:03d}",
                    action_id=f"action-{index:03d}",
                    contact_id=f"contact-{index % 4}",
                    causal_owner="self" if index % 2 else "world",
                    reward=-0.2,
                    trust_delta=-0.08,
                    reversible=index % 3 != 0,
                    opportunity_lost=index % 7 == 0,
                    observed_day=index,
                    hidden_cause="synthetic",
                ),
            )
            for index in range(1, 25)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_account_49_shadows(events, root / "c0.sqlite", root / "c5.sqlite")

        self.assertEqual(49, result["account_id"])
        self.assertEqual(24, result["event_count_per_arm"])
        self.assertTrue(result["same_event_ids"])
        self.assertTrue(result["separate_stores"])
        self.assertTrue(result["predictions_before_outcomes"])
        self.assertTrue(result["no_runtime_influence"])
        self.assertTrue(result["no_external_effects"])
        self.assertTrue(result["no_cross_arm_contamination"])
