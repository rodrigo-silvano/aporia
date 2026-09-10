from collections import Counter
import hashlib
import json
import unittest

from tests.Support.lacuna_experiment_v1 import AporiaLogicalLacunaExperimentV1
from tests.Support.splitmix64 import SplitMix64V1


class TestAporiaLogicalLacunaExperimentV1(unittest.TestCase):
    def test_splitmix64_matches_the_published_reference_vector(self):
        generator = SplitMix64V1('0000000000000000')
        self.assertEqual('e220a8397b1dcdaf', generator.nextHex())
        self.assertEqual('6e789e6aa1b965f4', generator.nextHex())
        self.assertEqual('06c45d188009454f', generator.nextHex())

    def test_corpus_is_deterministic_balanced_and_independent_of_unknown(self):
        experiment = AporiaLogicalLacunaExperimentV1()
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

        candidate_groups = {}
        for fixture in first:
            fp = hashlib.sha256(json.dumps(fixture['state'][:8]).encode('utf-8')).hexdigest()
            candidate_groups.setdefault(fp, []).append(fixture['positive'])
        self.assertEqual(2, len(candidate_groups))
        for labels in candidate_groups.values():
            self.assertEqual(25, len([l for l in labels if l]))
            self.assertEqual(25, len([l for l in labels if not l]))

    def test_experiment_engine_accepts_reference_candidate_and_rejects_commutative_mutation(self):
        experiment = AporiaLogicalLacunaExperimentV1()
        reference = experiment.runReference()
        commutative = experiment.run(AporiaLogicalLacunaExperimentV1.commutativeCandidate)

        self.assertTrue(reference['valid'])
        self.assertTrue(reference['passed'])
        self.assertEqual(50, reference['n_positive'])
        self.assertEqual(0, reference['n_negative'])
        self.assertEqual(1.0, reference['delta'])
        self.assertEqual(1.0, reference['bootstrap_lower_95'])
        self.assertTrue(commutative['valid'])
        self.assertFalse(commutative['passed'])
        self.assertEqual(0, commutative['n_positive'])
        self.assertEqual(0.0, commutative['delta'])

    def test_current_candidate_is_validly_falsified_without_changing_the_preregistered_rule(self):
        experiment = AporiaLogicalLacunaExperimentV1()
        first = experiment.run(AporiaLogicalLacunaExperimentV1.currentCandidate)
        second = experiment.run(AporiaLogicalLacunaExperimentV1.currentCandidate)

        self.assertEqual(first, second)
        self.assertTrue(first['valid'])
        self.assertFalse(first['passed'])
        self.assertEqual(0, first['n_positive'])
        self.assertEqual(0, first['n_negative'])
        self.assertEqual(0.0, first['delta'])
        self.assertEqual(0.0, first['bootstrap_lower_95'])
        self.assertEqual(0.0, first['bootstrap_upper_95'])
        self.assertLessEqual(first['bootstrap_lower_95'], 0.60)
        self.assertGreater(len([s for s in first['scores'] if s['d_random'] > 0.0]), 0)
        for score in first['scores']:
            self.assertTrue(score['valid'])
            self.assertEqual(0.0, score['d_passive'])
            self.assertEqual(0.125, score['d_order_only'])
            self.assertTrue(score['order_only_processed'])
            self.assertEqual(0.0, score['d_forked'])

    def test_candidate_receives_only_state_and_order(self):
        experiment = AporiaLogicalLacunaExperimentV1()
        argument_counts = []

        def candidate(state, order):
            argument_counts.append(2)
            return state

        result = experiment.run(candidate)
        self.assertTrue(result['valid'])
        self.assertEqual([2], list(set(argument_counts)))

    def test_candidate_cannot_write_outside_its_eight_dimensions(self):
        experiment = AporiaLogicalLacunaExperimentV1()
        with self.assertRaises(RuntimeError) as ctx:
            experiment.run(lambda state, order: {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0, 29: 0.0})
        self.assertIn('aporia_lacuna_experiment_candidate_dimension_invalid', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
