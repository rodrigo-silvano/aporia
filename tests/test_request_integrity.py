import json
from pathlib import Path
import unittest

from aporia.application.request_integrity import AporiaRequestIntegrity
from aporia.crypto import sha256_hex


class TestAporiaRequestIntegrity(unittest.TestCase):
    def test_cross_language_vectors_and_relevant_fields(self):
        path = Path(__file__).resolve().parent.parent / 'experiments/aporia-lacuna/fixtures/aporia_request_hash_vectors_r1.json'
        with open(path, 'r', encoding='utf-8') as f:
            vector = json.load(f)

        request = vector['request']
        context = vector['decision_context']
        request_hash = AporiaRequestIntegrity.model_request_hash(request)
        context['request_sha256'] = request_hash

        self.assertEqual(vector['expected']['model_request_sha256'], request_hash)
        self.assertEqual(
            vector['expected']['prompt_wire_sha256'],
            AporiaRequestIntegrity.prompt_wire_hash(
                str(request['instructions']),
                request['input']
            )
        )
        self.assertEqual(
            vector['expected']['wire_request_sha256'],
            sha256_hex(AporiaRequestIntegrity.canonical_json(request).encode('utf-8'))
        )
        self.assertEqual(
            vector['expected']['decision_context_sha256'],
            AporiaRequestIntegrity.decision_context_hash(context)
        )

        irrelevant = dict(request)
        irrelevant['volatile_trace'] = 'changed'
        self.assertEqual(request_hash, AporiaRequestIntegrity.model_request_hash(irrelevant))

        changed = dict(request)
        changed['previous_response_id'] = 'changed'
        self.assertNotEqual(request_hash, AporiaRequestIntegrity.model_request_hash(changed))

    def test_ordered_arrays_and_opaque_identifiers(self):
        left = {'tools': [{'name': 'a'}, {'name': 'b'}]}
        right = {'tools': [{'name': 'b'}, {'name': 'a'}]}
        self.assertNotEqual(
            AporiaRequestIntegrity.model_request_hash(left),
            AporiaRequestIntegrity.model_request_hash(right)
        )
        opaque = AporiaRequestIntegrity.opaque_identifier('secret', 'tenant-49')
        self.assertEqual(64, len(opaque))
        self.assertNotIn('tenant-49', opaque)


if __name__ == '__main__':
    unittest.main()
