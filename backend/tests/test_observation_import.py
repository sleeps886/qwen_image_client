import copy
import io
import json
import unittest
import zipfile

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services.inputs.observation_import import import_observations, MAX_BYTES


def observation():
    return {'candidates': [dict(candidate_id=f'O{i}', t=10, platform_id='A', array=array,
                                bearing_deg=30+i) for i, array in enumerate(('hull', 'tow'))]}


def package(data=None, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('input/observations.json', json.dumps(data or observation()))
        archive.writestr('input/platform.json', json.dumps({'platforms': [{'name': 'A'}]}))
        # Deliberately invalid truth: importing must not even parse it.
        archive.writestr('evaluation/reference.json', 'NOT JSON: SECRET TRUTH')
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return buffer.getvalue()


class ObservationImportTests(unittest.TestCase):
    def test_package_reads_observation_and_platform_not_truth(self):
        result = import_observations([('btr_target_001.zip', package())])
        self.assertEqual(result['candidates'], observation()['candidates'])
        self.assertEqual(result['platform_config']['platforms'][0]['name'], 'A')
        self.assertEqual(result['target_catalog'][0]['target_id'], 'btr_target_001')
        self.assertNotIn('SECRET TRUTH', json.dumps(result))
        self.assertEqual(result['file_name'], 'btr_target_001.json')

    def test_merge_namespaces_ids_and_groups(self):
        files = [('same.zip', package()), ('same.zip', package())]
        result = import_observations(files)
        self.assertEqual(len(set(p['candidate_id'] for p in result['candidates'])), 4)
        self.assertEqual(len(set(g['target_id'] for g in result['target_catalog'])), 2)
        self.assertEqual(result['batch_id'], import_observations(files)['batch_id'])

    def test_plain_json_compatibility(self):
        data = observation()
        data['target_catalog'] = [{'target_id': 'A038', 'candidate_ids': ['O0', 'O1']}]
        result = import_observations([('observations.json', json.dumps(data).encode())])
        self.assertEqual(result['target_catalog'], data['target_catalog'])

    def test_incompatible_platforms_rejected(self):
        data = observation()
        data['platform_config'] = {'platforms': [{'name': 'B'}]}
        with self.assertRaisesRegex(ValueError, '平台配置'):
            import_observations([('a.zip', package()), ('b.zip', package(data))])

    def test_invalid_inputs(self):
        cases = [('reference.json', b'{"evaluation_only":true,"candidates":[]}'),
                 ('bad.zip', b'bad'), ('bad.txt', b'bad'),
                 ('bad.zip', package(extra={'../outside.json': '{}'})),
                 ('bad.zip', package(extra={'other/observations.json': '{}'})),
                 ('big.json', b' ' * (MAX_BYTES+1))]
        invalid = copy.deepcopy(observation()); invalid['candidates'][1]['candidate_id'] = 'O0'
        cases.append(('bad.json', json.dumps(invalid).encode()))
        for item in cases:
            with self.subTest(name=item[0]), self.assertRaises(ValueError):
                import_observations([item])

    def test_endpoint_multipart_success_and_failure(self):
        with TestClient(app, base_url='http://127.0.0.1') as client:
            response = client.post('/api/incremental/import-observations',
                                   files=[('files', ('a.zip', package(), 'application/zip'))])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()['candidates']), 2)
            response = client.post('/api/incremental/import-observations',
                                   files=[('files', ('bad.zip', b'bad', 'application/zip'))])
            self.assertEqual(response.status_code, 400, response.text)


if __name__ == '__main__':
    unittest.main()

