"""Offline evaluator never chooses or rewrites a production mirror."""
import json
from pathlib import Path
import tempfile
import unittest

from backend.scripts.evaluate_inverse import evaluate


class InverseEvaluationTests(unittest.TestCase):
    def test_oracle_lower_bound_is_explicit_and_result_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            document={'coordinate_system':'lon_lat','tracks':[
                {'id':'N1','platform_id':'A','mirror_variant':v,'points':[
                    {'t':t,'x':122+v*.1,'y':30} for t in (0,10)]} for v in (0,1)]}
            path=root/'tracks.json';path.write_text(json.dumps(document),encoding='utf-8')
            before=path.read_bytes();truth=root/'labels.jsonl'
            truth.write_text('\n'.join(json.dumps({'platform_id':'A','target_id':'T1','time_s':t,'lat':30,'lon':122}) for t in (0,10)),encoding='utf-8')
            result=evaluate(root,truth)
            self.assertEqual(result['predicted_identity_count'],1)
            self.assertEqual(result['matched_targets_at_gate'],1)
            match=result['assignments'][0]
            self.assertTrue(match['oracle_side_given_for_evaluation_only'])
            self.assertAlmostEqual(match['oracle_best_alternative_rmse_m'],0)
            self.assertIn('primary_alternative_rmse_m',match)
            self.assertGreater(max(match['all_alternative_rmse_m']),9000)
            self.assertEqual(before,path.read_bytes())
            with self.assertRaises(ValueError):evaluate(root,truth,float('nan'))
