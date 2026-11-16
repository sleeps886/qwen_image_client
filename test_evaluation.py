import json
import unittest
import numpy as np
from evaluation import assignment,evaluate


def prediction():
    return {'coordinate_system':'lon_lat','tracks':[
        {'id':'AI-X','points':[{'t':0,'x':122,'y':30.1},{'t':10,'x':122.001,'y':30.1}]},
        {'id':'AI-Y','points':[{'t':0,'x':122,'y':30},{'t':10,'x':122.001,'y':30}]}]}


def truth():
    return '\n'.join(json.dumps({'platform_id':'A','target_id':tid,'time_s':t,'lat':lat,'lon':122+t*.0001})
        for tid,lat in [('T1',30),('T2',30.1)] for t in (0,5,10)).encode()


def semantic():
    return json.dumps({'platforms':[{'platform_id':'A','targets':[{'target_id':'T1'},{'target_id':'T2'}]}],
        'scenario_summary':{'target_total':2,'platform_state':'静止'}}).encode()


class EvaluationTests(unittest.TestCase):
    def test_global_assignment(self):
        self.assertEqual(assignment(np.array([[1.,2,100,100],[1.1,99,100,100]])),{1:0,0:1})

    def test_match_not_by_id(self):
        report=evaluate(prediction(),semantic(),truth())
        m=report['position_metrics']
        self.assertEqual(m['matched_targets'],2)
        self.assertEqual(m['rmse_m'],0)
        self.assertEqual(m['overall_time_coverage_percent'],100)
        self.assertEqual({p['truth_id']:p['predicted_id'] for p in m['matches']},{'T1':'AI-Y','T2':'AI-X'})

    def test_gap_and_unmatched(self):
        report=evaluate(prediction(),truth_raw=truth(),max_gap_s=1)
        self.assertAlmostEqual(report['position_metrics']['overall_time_coverage_percent'],66.67)
        p=prediction()
        for t in p['tracks']:
            for point in t['points']:point['x']+=1
        m=evaluate(p,truth_raw=truth())['position_metrics']
        self.assertIsNone(m['rmse_m']);self.assertEqual(m['matched_targets'],0)
        self.assertEqual(len(m['unmatched_truth_ids']),2)

    def test_semantic_only_and_limits(self):
        report=evaluate(prediction(),semantic_raw=semantic())
        self.assertTrue(report['target_count_correct'])
        self.assertIsNone(report['position_metrics'])
        p=prediction();p['coordinate_system']='local_xy_m'
        self.assertIn('旧AI结果',evaluate(p,truth_raw=truth())['position_status'])
        p=prediction();p['tracks'][1]['id']='AI-X';p['tracks'][1]['branch']='mirror'
        self.assertIn('候选分支',evaluate(p,truth_raw=truth())['position_status'])
        p=prediction();p['coordinate_system']='bearing_time'
        self.assertIsNone(evaluate(p,truth_raw=truth())['position_metrics'])
        with self.assertRaises(ValueError):evaluate(prediction(),truth_raw=b'bad json')

    def test_local_xy(self):
        p={'coordinate_system':'local_xy_m','coordinate_reference':{'platform_id':'A','origin_lat':30,'origin_lon':122},
           'tracks':[{'id':'X','points':[{'t':0,'x':0,'y':0},{'t':10,'x':0,'y':0}]}]}
        raw='\n'.join(json.dumps({'platform_id':'A','target_id':'T','time_s':t,'lat':30,'lon':122}) for t in (0,5,10)).encode()
        self.assertEqual(evaluate(p,truth_raw=raw)['position_metrics']['rmse_m'],0)


if __name__=='__main__':unittest.main()
