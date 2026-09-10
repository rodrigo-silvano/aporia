from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import unittest

from aporia.infrastructure.db import Connection
from aporia.infrastructure.experiments import AporiaEmpiricalStudy


class TestAporiaEmpiricalStudy(unittest.TestCase):
    def setUp(self):
        self.conn = Connection.in_memory()
        self.conn.executescript("""
        CREATE TABLE aporia_empirical_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            protocol_key TEXT NOT NULL, protocol_commitment TEXT NOT NULL, result_commitment TEXT,
            status TEXT NOT NULL, operator_user_id INTEGER NOT NULL, task_count INTEGER NOT NULL,
            arm_count INTEGER NOT NULL, model_count INTEGER NOT NULL, observation_count INTEGER NOT NULL DEFAULT 0,
            event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL,
            stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL,
            actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL,
            outcome TEXT NOT NULL, reason_code TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, run_id), UNIQUE(tenant_id, operation_id));
        CREATE TABLE aporia_empirical_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            run_id TEXT NOT NULL, task_ref TEXT NOT NULL, task_commitment TEXT NOT NULL, pair_ref TEXT NOT NULL,
            task_kind TEXT NOT NULL, arm TEXT NOT NULL, model_name TEXT NOT NULL, seed INTEGER NOT NULL,
            decision_label TEXT NOT NULL, decision_commitment TEXT NOT NULL, expected_label TEXT NOT NULL,
            correct INTEGER NOT NULL, confidence REAL NOT NULL, brier_score REAL NOT NULL,
            calibration_error REAL NOT NULL, critical_regression INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL, latency_ms INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(tenant_id, observation_id),
            UNIQUE(tenant_id, run_id, task_ref, arm, model_name));
        CREATE TABLE aporia_empirical_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            run_id TEXT NOT NULL, hypothesis_key TEXT NOT NULL, metric_name TEXT NOT NULL,
            control_arm TEXT NOT NULL, treatment_arm TEXT NOT NULL, sample_size INTEGER NOT NULL,
            control_mean REAL NOT NULL, treatment_mean REAL NOT NULL, absolute_effect REAL NOT NULL,
            relative_effect REAL, confidence_low REAL NOT NULL, confidence_high REAL NOT NULL,
            verdict TEXT NOT NULL, negative_result INTEGER NOT NULL, reason_codes_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, evaluation_id),
            UNIQUE(tenant_id, run_id, hypothesis_key));
        CREATE TABLE aporia_empirical_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, correction_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
            run_id TEXT NOT NULL, operator_user_id INTEGER NOT NULL, reason_code TEXT NOT NULL,
            previous_evaluations_json TEXT NOT NULL, corrected_evaluations_json TEXT NOT NULL,
            previous_evaluations_commitment TEXT NOT NULL, corrected_evaluations_commitment TEXT NOT NULL,
            event_name TEXT NOT NULL, event_version INTEGER NOT NULL, environment TEXT NOT NULL,
            stream TEXT NOT NULL, category TEXT NOT NULL, component TEXT NOT NULL, operation_id TEXT NOT NULL,
            actor_type TEXT NOT NULL, action_name TEXT NOT NULL, lifecycle_phase TEXT NOT NULL,
            outcome TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, correction_id), UNIQUE(tenant_id, operation_id));
        """)
        self.protocol_path = str(Path(__file__).resolve().parent.parent / 'experiments/aporia-lacuna/fixtures/aporia_c0_c5_v2.json')

    def test_balanced_results_produce_seven_paired_evaluations_and_advisory_gates(self):
        study = AporiaEmpiricalStudy(self.conn, self.protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        result = self._result_fixture()
        self.assertEqual(prepared['protocol_commitment'], result['protocol_commitment'])
        summary = study.ingest(9000000000000000001, 7, prepared['run_id'], result)
        self.assertEqual('completed', summary['status'])
        self.assertEqual(240, summary['observation_count'])
        self.assertEqual(7, len(summary['evaluations']))
        for evaluation in summary['evaluations']:
            self.assertGreaterEqual(evaluation['sample_size'], 8)
            self.assertEqual('inconclusive', evaluation['verdict'])
            self.assertTrue(evaluation['negative_result'])
            self.assertEqual([0.0, 0.0], evaluation['confidence_interval_95'])

        self.assertTrue(summary['advisory_gates']['no_critical_regressions'])
        self.assertTrue(summary['advisory_gates']['latency_within_budget'])
        self.assertTrue(summary['advisory_gates']['tokens_within_budget'])
        self.assertTrue(summary['advisory_gates']['advisory_did_not_dominate_confirmed_evidence'])
        self.assertEqual(summary, study.ingest(9000000000000000001, 7, prepared['run_id'], result))

    def test_independent_labels_reject_tampered_correctness_and_rollback(self):
        study = AporiaEmpiricalStudy(self.conn, self.protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        result = self._result_fixture()
        result['observations'][0]['correct'] = not result['observations'][0]['correct']

        with self.assertRaises(RuntimeError) as ctx:
            study.ingest(9000000000000000001, 7, prepared['run_id'], result)
        self.assertIn('aporia_empirical_observation_label_conflict', str(ctx.exception))
        self.assertEqual(0, int(self.conn.query('SELECT COUNT(*) FROM aporia_empirical_observations').fetchColumn(0)))
        self.assertEqual('prepared', str(self.conn.query('SELECT status FROM aporia_empirical_runs').fetchColumn(0)))

    def test_summary_rejects_a_protocol_different_from_the_prepared_run(self):
        study = AporiaEmpiricalStudy(self.conn, self.protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        v6_path = str(Path(self.protocol_path).parent / 'aporia_c0_c5_v6.json')
        v6 = AporiaEmpiricalStudy(self.conn, v6_path, 'test')

        with self.assertRaises(RuntimeError) as ctx:
            v6.summary(9000000000000000001, prepared['run_id'])
        self.assertIn('aporia_empirical_protocol_mismatch', str(ctx.exception))

    def test_bootstrap_samples_with_replacement_for_eight_pairs(self):
        study = AporiaEmpiricalStudy(self.conn, self.protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        summary = study.ingest(
            9000000000000000001,
            7,
            prepared['run_id'],
            self._result_fixture_with_noise_errors(2)
        )
        evaluation = [row for row in summary['evaluations'] if row['hypothesis'] == 'path_dependence_not_explained_by_noise'][0]

        self.assertEqual(0.25, evaluation['absolute_effect'])
        self.assertEqual(0.0, evaluation['confidence_interval_95'][0])
        self.assertGreater(evaluation['confidence_interval_95'][1], 0.25)
        self.assertEqual('inconclusive', evaluation['verdict'])
        self.assertIn('bootstrap_sampler_v2', evaluation['reason_codes'])

    def test_v4_requires_supported_functional_improvement_within_configured_token_budget(self):
        protocol_path = str(Path(self.protocol_path).parent / 'aporia_c0_c5_v4.json')
        study = AporiaEmpiricalStudy(self.conn, protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        result = self._result_fixture(protocol_path)
        protocol = self._load_protocol(protocol_path)
        tasks = {t['id']: t for t in protocol['tasks']}
        for obs in result['observations']:
            if obs['arm'] == 'C0' and tasks[obs['task_id']]['kind'] != 'factual_consistency':
                obs['decision'] = 'B' if obs['decision'] == 'A' else 'A'
                obs['correct'] = False
                obs['critical_regression'] = bool(tasks[obs['task_id']]['critical'])
            obs['input_tokens'] = 98 if obs['arm'] == 'C0' else 116
            obs['output_tokens'] = 2
            obs['total_tokens'] = obs['input_tokens'] + obs['output_tokens']

        summary = study.ingest(9000000000000000001, 7, prepared['run_id'], result)
        evaluation = [row for row in summary['evaluations'] if row['hypothesis'] == 'functional_improvement_over_baseline'][0]

        self.assertEqual(8, len(summary['evaluations']))
        self.assertEqual('supported', evaluation['verdict'])
        self.assertGreater(evaluation['treatment_mean'], evaluation['control_mean'])
        self.assertTrue(summary['advisory_gates']['functional_improvement_over_baseline'])
        self.assertTrue(summary['advisory_gates']['tokens_within_budget'])
        self.assertLessEqual(summary['advisory_gates']['token_overhead_ratio'], 1.25)

    def test_committed_v6_result_passes_scientific_and_operational_gates_when_provided(self):
        result_path = os.environ.get('APORIA_RESULT_PATH', '')
        if not result_path or not os.path.exists(result_path):
            self.skipTest('APORIA_RESULT_PATH unavailable')
        protocol_path = str(Path(self.protocol_path).parent / 'aporia_c0_c5_v6.json')
        with open(result_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        study = AporiaEmpiricalStudy(self.conn, protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        summary = study.ingest(9000000000000000001, 7, prepared['run_id'], result)

        self.assertEqual(240, summary['observation_count'])
        self.assertTrue(summary['advisory_gates']['functional_improvement_over_baseline'])
        self.assertTrue(summary['advisory_gates']['tokens_within_budget'])
        self.assertTrue(summary['advisory_gates']['no_critical_regressions'])
        self.assertTrue(summary['advisory_gates']['advisory_did_not_dominate_confirmed_evidence'])
        self.assertEqual('total_tokens', summary['advisory_gates']['token_overhead_metric'])
        self.assertGreater(
            summary['advisory_gates']['baseline_accuracy'],
            summary['advisory_gates']['treatment_accuracy']
        )

    def test_legacy_bootstrap_evaluation_is_corrected_atomically_and_idempotently(self):
        study = AporiaEmpiricalStudy(self.conn, self.protocol_path, 'test')
        prepared = study.prepare(9000000000000000001, 7)
        study.ingest(9000000000000000001, 7, prepared['run_id'], self._result_fixture_with_noise_errors(2))
        self.conn.prepare(
            """UPDATE aporia_empirical_evaluations
             SET confidence_low = 0.25, confidence_high = 0.25, verdict = 'supported',
                 reason_codes_json = '["paired_preregistered_tasks","fixed_seeds","bootstrap_5000"]'
             WHERE hypothesis_key = 'path_dependence_not_explained_by_noise'"""
        ).execute()
        self.conn.prepare(
            "UPDATE aporia_empirical_runs SET reason_code = 'hypotheses_evaluated' WHERE run_id = ?"
        ).execute([prepared['run_id']])

        corrected = study.correct_bootstrap(9000000000000000001, 7, prepared['run_id'])
        evaluation = [row for row in corrected['evaluations'] if row['hypothesis'] == 'path_dependence_not_explained_by_noise'][0]
        prev_row = self.conn.query('SELECT previous_evaluations_json, corrected_evaluations_json, previous_evaluations_commitment, corrected_evaluations_commitment FROM aporia_empirical_corrections').fetch()
        previous = json.loads(str(prev_row['previous_evaluations_json']))

        self.assertEqual('supported', [r for r in previous if r['hypothesis'] == 'path_dependence_not_explained_by_noise'][0]['verdict'])
        self.assertEqual('inconclusive', evaluation['verdict'])
        self.assertEqual(0.0, evaluation['confidence_interval_95'][0])
        self.assertGreater(evaluation['confidence_interval_95'][1], 0.25)
        self.assertIn('bootstrap_sampler_invalidated_v1', evaluation['reason_codes'])
        self.assertEqual(hashlib.sha256(str(prev_row['previous_evaluations_json']).encode('utf-8')).hexdigest(), prev_row['previous_evaluations_commitment'])
        self.assertEqual(hashlib.sha256(str(prev_row['corrected_evaluations_json']).encode('utf-8')).hexdigest(), prev_row['corrected_evaluations_commitment'])
        self.assertEqual(self._canonical(corrected['evaluations']), str(prev_row['corrected_evaluations_json']))
        self.assertEqual(1, len(corrected['corrections']))
        self.assertEqual(corrected, study.correct_bootstrap(9000000000000000001, 7, prepared['run_id']))
        self.assertEqual(1, int(self.conn.query('SELECT COUNT(*) FROM aporia_empirical_corrections').fetchColumn(0)))

    def _result_fixture(self, protocol_path: str | None = None) -> dict:
        protocol = self._load_protocol(protocol_path or self.protocol_path)
        observations = []
        for task in protocol['tasks']:
            for model in protocol['models']:
                for arm in protocol['arms']:
                    decision = str(task['expected'])
                    observations.append({
                        'task_id': task['id'],
                        'task_commitment': hashlib.sha256(self._canonical(task).encode('utf-8')).hexdigest(),
                        'arm': arm,
                        'model': model,
                        'seed': task['seed'],
                        'decision': decision,
                        'decision_commitment': hashlib.sha256(f"{task['id']}|{arm}|{model}".encode('utf-8')).hexdigest(),
                        'confidence': 0.8,
                        'correct': True,
                        'critical_regression': False,
                        'input_tokens': 3,
                        'output_tokens': 2,
                        'total_tokens': 5,
                        'latency_ms': 10,
                    })
        return {
            'protocol_key': protocol['protocol_key'],
            'protocol_commitment': hashlib.sha256(self._canonical(protocol).encode('utf-8')).hexdigest(),
            'observations': observations,
        }

    def _result_fixture_with_noise_errors(self, limit: int) -> dict:
        result = self._result_fixture()
        with open(self.protocol_path, 'r', encoding='utf-8') as f:
            protocol = json.load(f)
        tasks = {task['id']: task for task in protocol['tasks']}
        changed = 0
        for obs in result['observations']:
            task = tasks[obs['task_id']]
            if changed >= limit or task['kind'] != 'path' or obs['arm'] != 'C3':
                continue
            obs['decision'] = 'B' if task['expected'] == 'A' else 'A'
            obs['correct'] = False
            obs['critical_regression'] = bool(task['critical'])
            obs['decision_commitment'] = hashlib.sha256(f"{obs['decision_commitment']}|noise-error".encode('utf-8')).hexdigest()
            changed += 1
        self.assertEqual(limit, changed)
        return result

    def _load_protocol(self, protocol_path: str) -> dict:
        with open(protocol_path, 'r', encoding='utf-8') as f:
            protocol = json.load(f)
        if 'base_protocol' not in protocol:
            return protocol
        base = self._load_protocol(str(Path(protocol_path).parent / protocol['base_protocol']))
        tasks = []
        for task in base['tasks']:
            merged = dict(task)
            merged.update(protocol.get('task_overrides', {}).get(task['id'], {}))
            tasks.append(merged)
        protocol['tasks'] = tasks
        if protocol.get('task_overrides') == []:
            protocol['task_overrides'] = {}
        return protocol

    def _canonical(self, value) -> str:
        sorted_val = self._sort_value(value)
        return json.dumps(sorted_val, separators=(',', ':'), ensure_ascii=False)

    def _sort_value(self, value):
        if isinstance(value, dict):
            return {k: self._sort_value(v) for k, v in sorted(value.items())}
        if isinstance(value, list):
            return [self._sort_value(v) for v in value]
        return value


if __name__ == '__main__':
    unittest.main()
