import copy
import json
import unittest

from backend.app.services.sonar_evidence import resolve_pairs
from backend.tests.test_target_count import count_fixture


class PairTimingTests(unittest.TestCase):
    def setUp(self):
        self.evidence,_=count_fixture()

    def track(self,target,rows):
        return {'id':f'T{target}','platform_id':'A','pairs':[
            {'hull_id':f'I0R{r}P{target}','tow_id':f'I1R{r}P{target}'} for r in rows]}

    def resolve(self,tracks,**options):
        return resolve_pairs(json.dumps({'coordinate_system':'candidate_pairs','tracks':tracks}),
                             self.evidence,**options)

    def test_wrong_time_remains_strict_by_default_and_in_unknown_only_mode(self):
        track=self.track(0,[0,1,2]);track['pairs'][1]['tow_id']='I1R0P0'
        for options in ({},{'isolate_unknown':True}):
            with self.assertRaisesRegex(ValueError,'不同步'):self.resolve([track],**options)

    def test_isolation_preserves_other_targets_without_replacing_or_retiming(self):
        good=self.track(0,[0,1,2]);bad=self.track(1,[0,1,2])
        bad['pairs'][1]['tow_id']='I1R0P1';bad['pairs'][2]['tow_id']='I1R1P1'
        original=copy.deepcopy([good,bad])
        result=self.resolve([good,bad],isolate_unsynchronized=True)
        self.assertEqual([t['id'] for t in result['tracks']],['T0'])
        report=result['candidate_validation']
        self.assertEqual(report['rejected_pair_count'],3)
        mismatches=[p for p in report['rejected_pairs'] if p['reason']=='unsynchronized_pair_not_replaced']
        self.assertEqual(len(mismatches),2)
        self.assertNotEqual(mismatches[0]['hull_t'],mismatches[0]['tow_t'])
        self.assertEqual([good,bad],original)
        self.assertEqual([p['t'] for p in result['tracks'][0]['points']],[0,10,20])

    def test_duplicate_use_and_out_of_order_still_fail_in_partial_mode(self):
        for tracks in ([self.track(0,[0,1,2]),self.track(0,[0,1,2])],
                       [self.track(0,[2,1,0])],[self.track(0,[0,0,1])]):
            with self.subTest(tracks=tracks),self.assertRaises(ValueError):
                self.resolve(tracks,isolate_unsynchronized=True)

    def test_all_unsynchronized_has_no_drawable_result(self):
        track=self.track(0,[0,1])
        track['pairs'][0]['tow_id']='I1R1P0';track['pairs'][1]['tow_id']='I1R2P0'
        with self.assertRaisesRegex(ValueError,'不能生成轨迹'):
            self.resolve([track],isolate_unsynchronized=True)

    def test_discarded_singleton_does_not_reserve_candidate_from_later_valid_track(self):
        bad=self.track(0,[0,1]);bad['pairs'][1]['tow_id']='I1R0P0'
        result=self.resolve([bad,self.track(0,[0,1,2])],isolate_unsynchronized=True)
        self.assertEqual(len(result['tracks']),1)
        self.assertEqual(len(result['tracks'][0]['points']),3)
