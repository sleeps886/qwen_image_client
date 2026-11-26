import unittest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.services.association.mixed_prediction import create_mixed, prepare_mixed


class SimulationInputTests(unittest.TestCase):
    def test_ideal_observation_source_and_identity_isolation(self):
        points = [dict(candidate_id=f'o{i}', t=i//2, array=('hull', 'tow')[i%2],
                       platform_id='A', bearing_deg=30+i, quality='simulation_ideal',
                       source_kind='simulation_ideal_bearing', sigma_deg=1.6,
                       target_id='EvaluationIdentity', latitude=30, longitude=122)
                  for i in range(6)]
        state = create_mixed({'candidates': points})
        context, _ = prepare_mixed(state, [p['candidate_id'] for p in points], '')
        self.assertEqual(len(context['observations']), 6)
        self.assertTrue(all(p['quality']=='simulation_ideal' for p in context['observations']))
        self.assertNotIn('EvaluationIdentity', str(context))
        self.assertNotIn('latitude', str(context))

    def test_ideal_source_cannot_claim_gaussian_quality(self):
        with self.assertRaises(ValueError):
            create_mixed({'candidates': [dict(candidate_id='o', t=1, array='hull',
                platform_id='A', bearing_deg=30, quality='fitted',
                source_kind='simulation_ideal_bearing')]})

    def test_tool_page_assets_csp_and_origin_guard(self):
        with TestClient(create_app(), base_url='http://127.0.0.1') as client:
            page = client.get('/generator')
            self.assertEqual(page.status_code, 200)
            self.assertIn('frame-src http://127.0.0.1:8765', page.headers['Content-Security-Policy'])
            self.assertNotIn('frame-src', client.get('/').headers['Content-Security-Policy'])
            self.assertEqual(client.get('/assets/generator.js').status_code, 200)
            self.assertEqual(client.post('/api/generator/start',
                headers={'Origin': 'http://example.invalid'}).status_code, 403)
