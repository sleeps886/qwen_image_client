import copy
import unittest

from backend.app.services.association.identity_association import reconcile_identities
from backend.app.services.association.target_count import link_tracklets,audit_count,count_hypothesis,finalize_count
from backend.tests.test_target_count import count_fixture


def fixture():
    evidence,observations=count_fixture()
    lookup={p['id']:p for im in evidence['images'] for p in im['candidates']}
    for track in observations['tracks']:
        track.update(platform_id='A',branch='main')
        for p in track['points']:
            p.update(hull_deg=lookup[p['hull_candidate_id']]['bearing_deg'],
                     tow_deg=lookup[p['tow_candidate_id']]['bearing_deg'],
                     sample_index=lookup[p['hull_candidate_id']]['sample_index'])
    return evidence,observations


class IdentityAssociationTests(unittest.TestCase):
    def test_early_late_different_model_ids_become_one_supported_identity(self):
        evidence,observations=fixture();original=observations['tracks'][0]
        observations['tracks']=[{**original,'id':'T1','points':original['points'][:4]},
                                {**original,'id':'T2','points':original['points'][4:]}]
        frozen=copy.deepcopy(observations)
        result,report,_=reconcile_identities(observations,evidence)
        self.assertEqual(report['continuous_identity_count'],1)
        self.assertEqual(report['merged_model_id_boundaries'],1)
        self.assertEqual(result['tracks'][0]['model_target_ids'],['T1','T2'])
        self.assertEqual(len(result['tracks'][0]['points']),8)
        self.assertEqual(observations,frozen)
        for expected,actual in zip(original['points'],result['tracks'][0]['points']):
            self.assertEqual((expected['t'],expected['hull_deg'],expected['tow_deg']),
                             (actual['t'],actual['hull_deg'],actual['tow_deg']))

    def test_same_model_id_cannot_join_unrelated_ridges(self):
        evidence,observations=fixture();a,b=observations['tracks'][:2]
        observations['tracks']=[{**a,'id':'T1','points':a['points'][:4]},
                                {**b,'id':'T1','branch':'resume','points':b['points'][4:]}]
        result,report,_=reconcile_identities(observations,evidence)
        self.assertEqual(report['continuous_identity_count'],2)
        self.assertEqual(report['model_ids_split_across_ridges'],['T1'])
        self.assertEqual(len({t['id'] for t in result['tracks']}),2)
        self.assertTrue(all(len(t['points'])==4 for t in result['tracks']))

    def test_crossing_competition_is_split_not_automatically_bridged(self):
        evidence,observations=fixture()
        for im in evidence['images']:
            im['candidates']=[p for p in im['candidates'] if p['id'].endswith(('P0','P1'))]
            for p in im['candidates']:
                p['bearing_deg']=(60+5*p['sample_index']) if p['id'].endswith('P0') else (100-5*p['sample_index'])
        observations['tracks']=observations['tracks'][:1]
        result,report,_=reconcile_identities(observations,evidence)
        self.assertGreater(report['rejected_pair_count'],0)
        for track in result['tracks']:
            rows=[p['sample_index'] for p in track['points']]
            self.assertFalse(min(rows)<4 and max(rows)>4)
        self.assertFalse(report['unique_target_count_confirmed'])

    def test_missing_model_samples_do_not_get_filled(self):
        evidence,observations=fixture();observations['tracks']=observations['tracks'][:1]
        observations['tracks'][0]['points']=[p for p in observations['tracks'][0]['points'] if p['sample_index']!=4]
        result,report,_=reconcile_identities(observations,evidence)
        self.assertEqual(report['continuous_identity_count'],1)
        self.assertEqual(len(result['tracks']),2)
        self.assertFalse(any(p['sample_index']==4 for t in result['tracks'] for p in t['points']))

    def test_model_count_and_continuous_identity_count_remain_distinct(self):
        evidence,observations=fixture();a=observations['tracks'][0]
        observations['tracks']=[{**a,'id':'T1','points':a['points'][:4]},
                                {**a,'id':'T2','points':a['points'][4:]}]
        fragments=link_tracklets(evidence)
        count=audit_count('{}',evidence,fragments,count_hypothesis(fragments),observations)
        normalized,identity,_=reconcile_identities(observations,evidence)
        final=finalize_count(count,{'tracks':[{'id':'H1'},{'id':'H1'}]},identity)
        self.assertEqual(final['associated_target_count'],2)
        self.assertEqual(final['continuous_identity_count'],1)
        self.assertEqual(final['localized_target_count'],1)
        self.assertEqual(final['targets'][0]['model_ids'],['T1','T2'])

    def test_wrong_platform_is_not_repaired_by_relabelling(self):
        evidence,observations=fixture();observations['tracks'][0]['platform_id']='B'
        with self.assertRaises(ValueError):reconcile_identities(observations,evidence)
