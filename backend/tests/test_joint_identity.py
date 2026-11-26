"""Joint-identity contracts and numerical geometry; oracle replies are NOT LLM evals."""
import copy
import json
import unittest
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed,update_mixed,review_mixed,public_mixed
from backend.app.services.association.joint_identity import pair_evidence
from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
from backend.app.services.association.temporal_geometry import evaluate_motion
from backend.app.services.localization.forward_observation import GeoReference,observe
from backend.tests.model_plot_fixture import plot_answer


def joint_fixture(families=('cv',)):
    platform={'name':'A','start_ll':[30.,122.],'heading_deg':30.,'speed_kn':0.,'hull_m':[0.,0.],'tow_m':[-150.,0.]}
    ref=GeoReference(30.,122.);points=[];groups=[]
    parameters={'cv':[1.5,1.,-1.,2.5],'ca':[-2.,2.,1.2,-.8,.5,.8],'ct':[1.5,1.,-1.,2.,1.2]}
    for j,family in enumerate(families):
        by_array={array:[] for array in ('hull','tow')}
        xy=evaluate_motion(parameters[family],family,[i/8 for i in range(9)])*1000.
        for i,t in enumerate(range(0,801,100)):
            measured=observe(platform,ref.to_ll(tuple(map(float,xy[i]))),t,ref)
            for array in ('hull','tow'):
                cid=f'p{j}_{array}_{i}';by_array[array].append(cid)
                points.append({'candidate_id':cid,'platform_id':'A','array':array,'t':t,'bearing_deg':measured[array+'_deg'],
                               'bearing_error_assumption_deg':.15,'strength':.2 if family=='ct' else 10.,'quality':'fitted'})
        groups.append(by_array)
    state=create_mixed({'candidates':points,'platform_config':{'platforms':[platform]},
                        'target_catalog':[{'target_id':'PRIVATE_DIRECTORY','candidate_ids':groups[0]['hull']}]})
    return state,groups


def oracle_reply(state,groups,context):
    tracks=[];targets=[]
    for i,group in enumerate(groups):
        refs=[]
        for array in ('hull','tow'):
            name=f'N{len(tracks)+1}';refs.append(name)
            tracks.append({'track_ref':name,'observation_ids':[state['aliases'][cid] for cid in group[array]],'reason':'合成夹具观测片段'})
        targets.append({'target_ref':f'H{i+1}','fragment_refs':refs,'pairing_ref':'recompute','reason':'合成夹具跨阵候选，非真实模型推断'})
    return {'step_id':context['step_id'],'base_state_version':context['base_state_version'],'tracks':tracks,'target_hypotheses':targets}


class JointIdentityTests(unittest.TestCase):
    def test_evidence_is_available_before_first_model_and_no_source_answers(self):
        s,_=joint_fixture();ctx,prompt=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        self.assertIn('joint_identity',ctx);self.assertTrue(ctx['joint_identity']['pairings'])
        self.assertNotIn('PRIVATE_DIRECTORY',prompt);self.assertNotIn('p0_hull',prompt)
        self.assertTrue(ctx['identity_policy']['joint_before_confirmation']);self.assertLess(len(prompt),49000)
        self.assertEqual(s['calls'],0);self.assertEqual(s['targets'],{})

    def test_three_physical_hypotheses_are_not_six_array_fragments(self):
        s,groups=joint_fixture(('cv','ca','ct'));ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        v=validate_mixed(s,ctx,json.dumps(oracle_reply(s,groups,ctx)))
        new=update_mixed(s,ctx,v);view=public_mixed(new)
        self.assertEqual(len(view['fragments']),6);self.assertEqual(len(view['targets']),3)
        self.assertEqual(sum(len(t['history']) for t in view['fragments'].values()),54)
        self.assertTrue(all(fid.startswith('F') for fid in view['fragments']))
        self.assertTrue(all(tid.startswith('T') for tid in view['targets']))
        self.assertFalse(view['association_audit']['physical_target_count_confirmed'])
        adopted=review_mixed(new,new['version'],True);plot_context,_=prepare_model_plot(adopted)
        self.assertTrue(plot_context['spatial_references']['identity_gate_required'])
        self.assertEqual({c['id'] for c in plot_context['spatial_references']['candidates']},{'T001','T002','T003'})
        plotted=apply_model_plot(adopted,plot_context,json.dumps(plot_answer(plot_context)))
        self.assertEqual(plotted['plot_status'],'generated')

    def test_ray_only_two_times_never_promoted_to_final_target_space(self):
        s,groups=joint_fixture();ids=groups[0]['hull'][:2]+groups[0]['tow'][:2]
        ctx,_=prepare_mixed(s,ids);short=[{a:groups[0][a][:2] for a in ('hull','tow')}]
        new=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(oracle_reply(s,short,ctx))))
        new=review_mixed(new,new['version'],True);pc,_=prepare_model_plot(new)
        self.assertEqual(pc['spatial_references']['candidates'],[])
        self.assertEqual(new['targets']['T001']['status'],'needs_review')

    def test_duplicate_physical_occupation_rejected_without_mutation(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']]);a=oracle_reply(s,g,ctx)
        a['target_hypotheses'].append({**a['target_hypotheses'][0],'target_ref':'H2'});before=copy.deepcopy(s)
        with self.assertRaises(ValueError):validate_mixed(s,ctx,json.dumps(a))
        self.assertEqual(s,before)

    def test_target_rollback_and_legacy_response_cannot_enable_spatial_pairing(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']]);a=oracle_reply(s,g,ctx)
        new=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(a)))
        rolled=review_mixed(new,new['version'],False,'不采用');self.assertEqual(rolled['targets'],{})
        a.pop('target_hypotheses');legacy=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(a)))
        legacy=review_mixed(legacy,legacy['version'],True);pc,_=prepare_model_plot(legacy)
        self.assertEqual(pc['spatial_references']['candidates'],[])

    def test_failed_temporal_pair_remains_unresolved_not_a_geometry_success(self):
        s,g=joint_fixture();left=[p for p in s['points'] if p['array']=='hull'];right=[copy.deepcopy(p) for p in s['points'] if p['array']=='tow']
        for i,p in enumerate(right):p['bearing_deg']=10. if i%2 else 160.
        self.assertFalse(pair_evidence(left,right,s['platform_config'])['eligible_for_spatial'])

    def test_drawing_can_request_identity_review_without_changing_membership(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        new=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(oracle_reply(s,g,ctx))));new=review_mixed(new,new['version'],True)
        pc,_=prepare_model_plot(new);answer=plot_answer(pc);answer['review_requests']=[{'target_id':'T001','reason':'需复核阵列身份'}]
        result=apply_model_plot(new,pc,json.dumps(answer))
        self.assertEqual(result['tracks']['T001']['history'],new['tracks']['T001']['history'])
        self.assertEqual(result['targets'],new['targets']);self.assertEqual(result['plot_status'],'awaiting_association_recheck')
        self.assertFalse(result['rounds'][-1]['model_plot']['spatial_result']['tracks'])
        with self.assertRaises(ValueError):prepare_model_plot(result)

    def test_same_selected_set_order_does_not_change_numerical_evidence(self):
        s,_=joint_fixture();ids=[p['candidate_id'] for p in s['points']]
        self.assertEqual(prepare_mixed(s,ids),prepare_mixed(s,list(reversed(ids))))

    def test_candidate_references_bind_exact_support_and_misquoted_pair_is_held(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        pair=next(p for p in ctx['joint_identity']['pairings'] if p['eligible_for_spatial'])
        a={'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
           'tracks':[{'track_ref':f'N{i+1}','candidate_ref':ref,'reason':'引用候选实测集合'} for i,ref in enumerate(pair['fragment_refs'])],
           'target_hypotheses':[{'target_ref':'H1','fragment_refs':['N1','N2'],'pairing_ref':pair['reference_id'],'reason':'完整匹配跨阵候选'}]}
        result=validate_mixed(s,ctx,json.dumps(a));self.assertTrue(result['targets']['T001']['evidence']['eligible_for_spatial'])
        a['target_hypotheses'][0]['pairing_ref']='P999'
        result=validate_mixed(s,ctx,json.dumps(a));self.assertFalse(result['targets']['T001']['evidence']['eligible_for_spatial'])
        self.assertEqual(result['targets']['T001']['evidence']['status'],'pairing_reference_mismatch')

    def test_conflicting_unassigned_withdraws_point_without_whole_round_failure(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']]);a=oracle_reply(s,g,ctx)
        oid=a['tracks'][0]['observation_ids'][0];a['unassigned']=[oid,oid]
        v=validate_mixed(s,ctx,json.dumps(a));cid=g[0]['hull'][0]
        self.assertIn(cid,v['unassigned_ids']);self.assertTrue(v['tracks'])
        self.assertTrue(all(p['candidate_id']!=cid for tr in v['tracks'].values() for p in tr['history']))
        self.assertTrue(any(w['code']=='conflicting_unassigned' for w in v['warnings']))

    def test_legacy_collection_requires_joint_recheck_before_new_paid_plot(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        state=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(oracle_reply(s,g,ctx))))
        state=review_mixed(state,state['version'],True);state.pop('joint_identity_version')
        with self.assertRaisesRegex(ValueError,'旧版集合'):prepare_model_plot(state)

    def test_legacy_saved_plot_recovery_cannot_bypass_new_identity_gate(self):
        s,g=joint_fixture();ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        state=update_mixed(s,ctx,validate_mixed(s,ctx,json.dumps(oracle_reply(s,g,ctx))));state=review_mixed(state,state['version'],True)
        pc,_=prepare_model_plot(state);reply=plot_answer(pc);state.pop('joint_identity_version')
        recovered=apply_model_plot(state,pc,json.dumps(reply))
        self.assertFalse(recovered['rounds'][-1]['model_plot']['spatial_result']['tracks'])
        self.assertEqual(recovered['plot_status'],'awaiting_spatial')


if __name__=='__main__':unittest.main()
