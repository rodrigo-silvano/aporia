from collections import Counter
import hashlib
import json
import unittest

from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernelV2
from tests.Support.lacuna_experiment_v2 import AporiaLogicalLacunaExperimentV2


class TestAporiaLogicalLacunaExperimentV2(unittest.TestCase):
    def test_corpus_is_deterministic_balanced_and_structurally_labelled(self):
        experiment = AporiaLogicalLacunaExperimentV2()
        first = experiment.fixtures()
        second = experiment.fixtures()

        self.assertEqual(first, second)
        self.assertEqual(100, len(first))
        self.assertEqual(100, len(set(f['id'] for f in first)))
        counts = Counter(f['family'] for f in first)
        self.assertEqual({'P0': 25, 'P1': 25, 'N0': 25, 'N1': 25}, dict(counts))
        self.assertNotEqual(['P0'] * 25, [f['family'] for f in first[:25]])
        self.assertEqual(50, len([f for f in first if f['positive']]))
        self.assertEqual(50, len([f for f in first if f['state'][0] > 0.0]))

        state_fingerprints = [
            hashlib.sha256(json.dumps(f['state']).encode('utf-8')).hexdigest()
            for f in first
        ]
        self.assertEqual(100, len(set(state_fingerprints)))

        for fixture in first:
            self.assertEqual(32, len(fixture['state']))
            derived_positive = (int(fixture['state'][3]) == 1 and ((int(fixture['state'][1]) & int(fixture['state'][2])) != 0))
            self.assertEqual(fixture['positive'], derived_positive)

    def test_kernel_produces_the_preregistered_order_effect_only_for_causal_requirements(self):
        positive = [0.0] * 12
        positive[1] = 1.0
        positive[2] = 3.0
        positive[3] = 1.0
        negative = list(positive)
        negative[2] = 6.0
        qr = [AporiaLacunaKernelV2.REQUIREMENT_FOCUS, AporiaLacunaKernelV2.DEPENDENCY_PROJECTION]
        rq = list(reversed(qr))

        positive_qr = AporiaLacunaKernelV2.sequence(positive, qr)
        positive_rq = AporiaLacunaKernelV2.sequence(positive, rq)
        negative_qr = AporiaLacunaKernelV2.sequence(negative, qr)
        negative_rq = AporiaLacunaKernelV2.sequence(negative, rq)

        self.assertEqual(1.0, positive_qr[7])
        self.assertEqual(-1.0, positive_qr[8])
        self.assertEqual(-1.0, positive_qr[9])
        self.assertEqual(0.0, positive_qr[10])
        self.assertEqual(1.0, positive_rq[7])
        self.assertEqual(-1.0, positive_rq[10])
        self.assertEqual(-1.0, positive_rq[11])
        self.assertEqual(negative_qr, negative_rq)
        self.assertEqual(0.0, negative_qr[7])
        self.assertEqual(-1.0, negative_qr[10])
        self.assertEqual(-1.0, negative_qr[11])

    def test_experiment_accepts_reference_and_current_candidate(self):
        experiment = AporiaLogicalLacunaExperimentV2()
        reference = experiment.runReference()
        current = experiment.run(AporiaLogicalLacunaExperimentV2.currentCandidate)

        for result in [reference, current]:
            self.assertTrue(result['valid'])
            self.assertTrue(result['passed'])
            self.assertEqual(50, result['n_positive'])
            self.assertEqual(0, result['n_negative'])
            self.assertEqual(1.0, result['delta'])
            self.assertEqual(1.0, result['bootstrap_lower_95'])
            self.assertEqual(1.0, result['bootstrap_upper_95'])

    def test_preregistered_mutations_are_rejected(self):
        experiment = AporiaLogicalLacunaExperimentV2()
        commutative = experiment.run(AporiaLogicalLacunaExperimentV2.commutativeCandidate)
        unknown = experiment.run(AporiaLogicalLacunaExperimentV2.unknownCandidate)
        missing_mask = experiment.run(AporiaLogicalLacunaExperimentV2.missingMaskCandidate)

        self.assertTrue(commutative['valid'])
        self.assertFalse(commutative['passed'])
        self.assertEqual(0, commutative['n_positive'])
        self.assertEqual(0, commutative['n_negative'])
        self.assertTrue(unknown['valid'])
        self.assertFalse(unknown['passed'])
        self.assertEqual(25, unknown['n_positive'])
        self.assertEqual(25, unknown['n_negative'])
        self.assertEqual(0.0, unknown['delta'])
        self.assertTrue(missing_mask['valid'])
        self.assertFalse(missing_mask['passed'])
        self.assertGreater(missing_mask['n_positive'], 0)
        self.assertLess(missing_mask['n_positive'], 45)

    def test_candidate_receives_only_twelve_dimensions_and_two_arguments(self):
        experiment = AporiaLogicalLacunaExperimentV2()
        argument_counts = []
        dimension_counts = []

        def candidate(state, order):
            argument_counts.append(2)
            dimension_counts.append(len(state))
            return state

        result = experiment.run(candidate)
        self.assertTrue(result['valid'])
        self.assertEqual([2], list(set(argument_counts)))
        self.assertEqual([12], list(set(dimension_counts)))

    def test_candidate_cannot_write_outside_its_twelve_dimensions(self):
        experiment = AporiaLogicalLacunaExperimentV2()
        with self.assertRaises(RuntimeError) as ctx:
            experiment.run(lambda state, order: {**{i: state[i] for i in range(12)}, 29: 0.0})
        self.assertIn('aporia_lacuna_v2_candidate_dimension_invalid', str(ctx.exception))

    def test_kernel_rejects_fractional_masks_and_parent_counts(self):
        state = [0.0] * 12
        state[1] = 1.5
        state[2] = 3.0
        state[3] = 1.0

        with self.assertRaises(RuntimeError) as ctx:
            AporiaLacunaKernelV2.apply(state, AporiaLacunaKernelV2.REQUIREMENT_FOCUS)
        self.assertEqual('aporia_lacuna_v2_mask_invalid', str(ctx.exception))

        state[1] = 1.0
        state[3] = 0.5
        with self.assertRaises(RuntimeError) as ctx:
            AporiaLacunaKernelV2.apply(state, AporiaLacunaKernelV2.REQUIREMENT_FOCUS)
        self.assertEqual('aporia_lacuna_v2_parent_count_invalid', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
