import copy
import unittest
from unittest.mock import patch
from backend.app.services.jobs.inversion_control import InversionControl
from backend.app.services.localization.batch_inverse import fit_pair,fit_proposals,support_for,select_exclusive,_synchronous_window
from backend.tests.test_full_inverse import inverse_fixture
from backend.app.services.association.ridge_hypotheses import propose_pairs


class InverseInitializationTests(unittest.TestCase):
    def test_independently_unfolded_arrays_do_not_erase_valid_folded_observations(self):
        context,pair,_=inverse_fixture();before=copy.deepcopy(pair)
        for point in pair['tow']['points']:point['unfolded_deg']=-point['bearing_deg']
        shifted=copy.deepcopy(pair)
        result=fit_pair(pair,context,InversionControl())
        self.assertIsNotNone(result)
        self.assertLess(result['variants'][0]['rmse_deg'],.01)
        self.assertGreaterEqual(len(result['variants']),2)
        self.assertEqual(pair,shifted)
        self.assertEqual([p['bearing_deg'] for p in pair['tow']['points']],[p['bearing_deg'] for p in before['tow']['points']])

    def test_synchronous_window_adds_hypothesis_without_replacing_full_ridge_or_inventing_points(self):
        context,pair,_=inverse_fixture();pair['hull']['points']=pair['hull']['points'][16:]
        pair['common_rows']=list(range(16,33));before=copy.deepcopy(pair)
        window=_synchronous_window(pair)
        self.assertIsNotNone(window)
        self.assertEqual(len(window['hull']['points']),17)
        self.assertEqual(len(window['tow']['points']),17)
        self.assertTrue(all(p in pair['tow']['points'] for p in window['tow']['points']))
        self.assertEqual(pair,before)
        def fit(candidate,*args,**kwargs):
            return {'id':candidate['id'],'variants':[]} if candidate['id'].endswith(':sync') else None
        with patch('backend.app.services.localization.batch_inverse.fit_pair',side_effect=fit):
            result=fit_proposals({'pairs':[pair],'budget_excluded':0},{},context,InversionControl())
        self.assertEqual(result['attempted'],1)
        self.assertEqual(result['fit_attempts'],2)
        self.assertEqual(result['rejected'],0)
        self.assertEqual(result['candidates'][0]['fitting_scope'],'synchronous_window')
        self.assertEqual(pair,before)

    def test_short_overlap_and_no_tail_do_not_expand_fit_budget(self):
        _,pair,_=inverse_fixture()
        self.assertIsNone(_synchronous_window(pair))
        pair['common_rows']=[0,1,2,3,4]
        self.assertIsNone(_synchronous_window(pair))

    def test_support_does_not_count_extrapolated_peaks_outside_fit_window(self):
        context,pair,_=inverse_fixture()
        fit=fit_pair(pair,context,InversionControl())
        fit['start_t']=200.;fit['end_t']=400.
        evidence={'images':[{'array':a,'candidates':pair[a]['points']} for a in ('hull','tow')]}
        lookup={p['id']:p for a in ('hull','tow') for p in pair[a]['points']}
        support,error=support_for(fit,evidence,context)
        self.assertTrue(support)
        self.assertTrue(all(200<=lookup[i]['t']<=400 for i in support))
        self.assertLess(error,.01)

    def test_single_observed_target_remains_one_identity_not_a_forced_four(self):
        context,pair,_=inverse_fixture()
        evidence={'images':[{'array':a,'candidates':pair[a]['points']} for a in ('hull','tow')]}
        control=InversionControl()
        fitted=fit_proposals({'pairs':[pair],'budget_excluded':0},evidence,context,control)
        selected=select_exclusive(fitted,evidence,context,control)
        self.assertEqual(len(selected['selected']),1)
        self.assertFalse(selected['target_count_confirmed'])

    def test_asymmetric_weak_ridge_can_propose_five_synchronous_points(self):
        _,pair,_=inverse_fixture(count=50)
        hull={**pair['hull'],'array':'hull','platform_id':'A','support':5,'points':pair['hull']['points'][10:15]}
        tow={**pair['tow'],'array':'tow','platform_id':'A','support':50}
        for track in (hull,tow):track.update(start_t=track['points'][0]['t'],end_t=track['points'][-1]['t'])
        result=propose_pairs({'tracks':[hull,tow]},InversionControl())
        self.assertEqual(len(result['pairs']),1)
        self.assertTrue(result['pairs'][0]['sparse_dual_support'])
        self.assertEqual(result['retained_sparse_pairs'],1)
        hull={**hull,'points':hull['points'][:3]}
        self.assertEqual(propose_pairs({'tracks':[hull,tow]},InversionControl())['pairs'],[])

    def test_two_short_ridges_do_not_bypass_normal_synchronous_gate(self):
        _,pair,_=inverse_fixture(count=5)
        tracks=[]
        for array in ('hull','tow'):
            p=pair[array]['points'];tracks.append({**pair[array],'array':array,'platform_id':'A','support':5,
                                                  'start_t':p[0]['t'],'end_t':p[-1]['t']})
        self.assertEqual(propose_pairs({'tracks':tracks},InversionControl())['pairs'],[])

    def test_single_array_support_never_counts_as_a_localized_identity(self):
        ids=[f'p{i}' for i in range(20)]
        evidence={'images':[{'array':'tow','candidates':[{'id':i} for i in ids]},
                            {'array':'hull','candidates':[]}]}
        candidate={'id':'one_array','variants':[{'parameters':[0]*4}]}
        with patch('backend.app.services.localization.batch_inverse.support_for',return_value=(ids,.1)):
            result=select_exclusive({'candidates':[candidate]},evidence,{},InversionControl())
        self.assertEqual(result['selected'],[])


if __name__=='__main__':unittest.main()
