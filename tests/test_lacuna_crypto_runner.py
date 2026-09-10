from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from aporia.crypto import canonical_json, sha256_hex
from aporia.infrastructure.db import Connection
from aporia.infrastructure.lacuna_crypto_runner import AporiaLacunaCryptographicRunner
from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernelV2


class TestAporiaLacunaCryptographicRunner(unittest.TestCase):
    SECRET = 'cryptographic-runner-test-secret-000000000000000000000000'

    def setUp(self):
        self.sidecar_path = Path(__file__).resolve().parent.parent / 'experiments/aporia-lacuna/sidecar/one_shot.py'
        self.auth_secret = sha256_hex(f"aporia-lacuna-cryptographic-v1|{self.SECRET}")

    def test_runner_returns_only_ciphertexts_commitments_and_a_validated_receipt(self):
        state = self._positive_state()
        runner = AporiaLacunaCryptographicRunner(Connection.in_memory(), self.SECRET, self.sidecar_path)

        first = runner.execute(49, 'q_then_r_v2', state)
        second = runner.execute(49, 'q_then_r_v2', state)

        expected = list(state)
        seq = AporiaLacunaKernelV2.sequence(state[:12], [
            AporiaLacunaKernelV2.REQUIREMENT_FOCUS,
            AporiaLacunaKernelV2.DEPENDENCY_PROJECTION,
        ])
        for index, value in enumerate(seq):
            expected[index] = value

        self.assertEqual(sha256_hex(canonical_json(state).encode('utf-8')), first['input_commitment'])
        self.assertEqual(sha256_hex(canonical_json(expected).encode('utf-8')), first['output_commitment'])
        self.assertEqual(3, first['changed_dimensions'])
        self.assertNotEqual(first['input_ciphertext'], first['destroyed_ciphertext'])
        self.assertNotEqual(first['input_ciphertext'], second['input_ciphertext'])
        self.assertNotEqual(first['latent_id'], second['latent_id'])
        self.assertIn(first['memory_lock_status'], ['locked', 'unsupported'])

        serialized = json.dumps(first)
        self.assertNotIn(canonical_json(state), serialized)
        self.assertNotIn('capability_token', serialized)
        self.assertNotIn('"key"', serialized)

    def test_capability_is_bound_to_tenant_and_cannot_be_replayed_after_process_exit(self):
        proc, hello = self._start_sidecar()
        state = self._positive_state()
        latent_id = '244f2e44-220e-4f69-92b1-bca110a08110'
        token = 'a' * 64

        prepare = self._sign({
            'protocol': 1,
            'op': 'prepare',
            'challenge': hello['challenge'],
            'tenant_id': 49,
            'latent_id': latent_id,
            'instrument': 'q_then_r_v2',
            'capability_token': token,
            'state': state,
        })
        self._write(proc, prepare)
        sealed = self._read(proc)
        self.assertEqual('sealed', sealed['status'])

        cross_tenant = self._sign({
            'protocol': 1,
            'op': 'consume',
            'challenge': hello['challenge'],
            'tenant_id': 73,
            'latent_id': latent_id,
            'instrument': 'q_then_r_v2',
            'capability_token': token,
            'nonce': sealed['nonce'],
            'ciphertext': sealed['ciphertext'],
        })
        self._write(proc, cross_tenant)
        proc.stdin.close()
        rejected = self._read(proc)
        self.assertEqual('rejected', rejected['status'])
        self.assertEqual('aporia_crypto_capability_invalid', rejected['error_code'])
        proc.wait(timeout=3)
        self.assertEqual(1, proc.returncode)

        replay_proc, replay_hello = self._start_sidecar()
        replay = dict(cross_tenant)
        replay['tenant_id'] = 49
        replay['challenge'] = replay_hello['challenge']
        replay = self._sign(replay)
        self._write(replay_proc, replay)
        replay_proc.stdin.close()
        replay_rejected = self._read(replay_proc)
        self.assertEqual('rejected', replay_rejected['status'])
        self.assertEqual('aporia_crypto_prepare_invalid', replay_rejected['error_code'])
        replay_proc.wait(timeout=3)
        self.assertEqual(1, replay_proc.returncode)

    def test_crash_after_sealing_leaves_no_process_or_reusable_capability(self):
        proc, hello = self._start_sidecar()
        prepare = self._sign({
            'protocol': 1,
            'op': 'prepare',
            'challenge': hello['challenge'],
            'tenant_id': 49,
            'latent_id': '55a3c568-1ce8-4d83-81ce-48421b503b8e',
            'instrument': 'r_then_q_v2',
            'capability_token': 'b' * 64,
            'state': self._positive_state(),
        })
        self._write(proc, prepare)
        sealed = self._read(proc)
        self.assertEqual('sealed', sealed['status'])
        self.assertNotEqual('', sealed['ciphertext'])

        proc.kill()
        proc.wait(timeout=3)
        self.assertNotEqual(0, proc.returncode)

    def test_successfully_consumed_capability_cannot_be_used_by_another_process(self):
        proc, hello = self._start_sidecar()
        latent_id = '2ee374fa-b537-4f36-b08a-dcd99c680bcc'
        token = 'c' * 64
        prepare = self._sign({
            'protocol': 1,
            'op': 'prepare',
            'challenge': hello['challenge'],
            'tenant_id': 49,
            'latent_id': latent_id,
            'instrument': 'q_then_r_v2',
            'capability_token': token,
            'state': self._positive_state(),
        })
        self._write(proc, prepare)
        sealed = self._read(proc)

        consume = self._sign({
            'protocol': 1,
            'op': 'consume',
            'challenge': hello['challenge'],
            'tenant_id': 49,
            'latent_id': latent_id,
            'instrument': 'q_then_r_v2',
            'capability_token': token,
            'nonce': sealed['nonce'],
            'ciphertext': sealed['ciphertext'],
        })
        self._write(proc, consume)
        proc.stdin.close()
        receipt = self._read(proc)
        self.assertEqual('consumed', receipt['status'])
        proc.wait(timeout=3)
        self.assertEqual(0, proc.returncode)

        replay_proc, replay_hello = self._start_sidecar()
        replay_consume = dict(consume)
        replay_consume['challenge'] = replay_hello['challenge']
        replay_consume = self._sign(replay_consume)
        self._write(replay_proc, replay_consume)
        replay_proc.stdin.close()
        rejected = self._read(replay_proc)
        self.assertEqual('rejected', rejected['status'])
        self.assertEqual('aporia_crypto_prepare_invalid', rejected['error_code'])
        replay_proc.wait(timeout=3)
        self.assertEqual(1, replay_proc.returncode)

    def test_sidecar_source_has_no_database_network_or_plaintext_logging_surface(self):
        source = self.sidecar_path.read_text(encoding='utf-8')
        for forbidden in ['sqlite3', 'cursor', 'socket', 'urllib', 'http', 'requests', 'syslog', 'logging']:
            self.assertNotIn(forbidden, source)

    def _positive_state(self) -> list[float]:
        state = [0.0] * 32
        state[0] = 1.0 / 6.0
        state[1] = 1.0
        state[2] = 3.0
        state[3] = 1.0
        state[4] = 0.2
        state[5] = 0.6
        state[6] = 0.1
        return state

    def _start_sidecar(self):
        env = dict(os.environ)
        env['APORIA_SIDECAR_SECRET'] = self.auth_secret
        proc = subprocess.Popen(
            [sys.executable, str(self.sidecar_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        hello_line = proc.stdout.readline()
        hello = json.loads(hello_line)
        self.assertEqual('hello', hello['status'])
        self.assertTrue(hello['core_dumps_disabled'])
        return proc, hello

    def _sign(self, message: dict) -> dict:
        msg = dict(message)
        msg.pop('auth', None)
        canonical = canonical_json(msg)
        msg['auth'] = hmac.new(self.auth_secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256).hexdigest()
        return msg

    def _write(self, proc: subprocess.Popen, message: dict):
        payload = canonical_json(message) + "\n"
        proc.stdin.write(payload)
        proc.stdin.flush()

    def _read(self, proc: subprocess.Popen) -> dict:
        line = proc.stdout.readline()
        return json.loads(line)


if __name__ == '__main__':
    unittest.main()
