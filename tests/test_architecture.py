from pathlib import Path
import unittest


class TestAporiaArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent

    def _source(self, rel_path: str) -> str:
        path = self.root / rel_path
        self.assertTrue(path.exists(), f"File {rel_path} does not exist")
        return path.read_text(encoding='utf-8')

    def test_runtime_influence_is_stopped_before_tenant_mode_resolution(self):
        source = self._source('aporia/application/runtime_mode_resolver.py')
        self.assertIn('AporiaIndependentControlPlane.GLOBAL', source)
        self.assertIn('AporiaIndependentControlPlane.TENANT', source)
        self.assertIn('AporiaIndependentControlPlane.RUNTIME_INFLUENCE', source)
        self.assertTrue(
            source.index('AporiaIndependentControlPlane.RUNTIME_INFLUENCE')
            < source.index('AporiaRuntimeMode.for_tenant')
        )

    def test_memory_and_ontology_switches_guard_their_write_boundaries(self):
        events = self._source('aporia/api/events_endpoint.py')
        self.assertIn('AporiaIndependentControlPlane.MEMORY_WRITES', events)
        self.assertIn('AporiaIndependentControlPlane.ONTOLOGY', events)
        self.assertIn('aporia_write_kill_switch', events)
        self.assertTrue(events.index('aporia_write_kill_switch') < events.index('fabric = AporiaEventFabric'))

    def test_prepared_and_streaming_requests_carry_the_independent_control_snapshot(self):
        gateway_path = self.root / 'services/agent-runtime/gateway/main.py'
        if not gateway_path.exists():
            self.skipTest("services/agent-runtime is not in repository")
        gateway = self._source('services/agent-runtime/gateway/main.py')
        hosted = self._source('services/agent-runtime/hosted/prepare.py')
        runtime = self._source('services/agent-runtime/gateway/runtime.py')

        self.assertIn('aporia_control_state', gateway)
        self.assertIn('message["aporia_control_state"] = control_state', gateway)
        self.assertTrue(
            gateway.index('message["aporia_control_state"] = control_state')
            < gateway.index('await self.upstream.send(json.dumps(forwarded')
        )
        self.assertIn('engine.model_settings["_aporia_control_state"] = control_state', hosted)
        for key in [
            'APORIA_KILL_RUNTIME_INFLUENCE',
            'APORIA_KILL_MEMORY_WRITES',
            'APORIA_KILL_ONTOLOGY',
            'APORIA_KILL_HIRT_ADVISORY',
        ]:
            self.assertIn(key, runtime)

    def test_authenticated_websocket_attestation_precedes_ecological_prediction_seal(self):
        gateway_path = self.root / 'services/agent-runtime/gateway/main.py'
        if gateway_path.exists():
            gateway = self._source('services/agent-runtime/gateway/main.py')
            self.assertIn('await hub.send(message, authenticated_websocket=True)', gateway)
            self.assertIn('"authenticated_websocket": authenticated_websocket', gateway)
            self.assertIn('"authentication_basis": "origin_cookie_csrf_session" if authenticated_websocket else "unverified"', gateway)

        program = self._source('aporia/infrastructure/ecological.py')
        self.assertIn('AporiaEcologicalTransportPrecondition', program)
        self.assertIn('transport_attestation_hash', program)
        self.assertIn('aporia_prospective_late_prediction', program)

    def test_transport_audit_uses_opaque_references_and_stable_envelope(self):
        migration = self._source('platform/database/migrations/2026_08_24_aporia_ecological_transport_precondition_r1.sql')
        precondition = self._source('aporia/infrastructure/ecological.py')

        self.assertNotIn('session_public_id', migration)
        self.assertIn('session_ref', migration)
        self.assertIn('request_ref', migration)
        self.assertIn('event_id', migration)
        self.assertIn('operation_id', migration)
        self.assertIn('initiator_type', migration)
        self.assertIn('executor_type', migration)
        self.assertIn('source_channel', migration)
        self.assertIn("aporia.ecological.transport.verified", precondition)
        self.assertIn("authenticated_websocket_and_session_turn_binding", precondition)


if __name__ == '__main__':
    unittest.main()
