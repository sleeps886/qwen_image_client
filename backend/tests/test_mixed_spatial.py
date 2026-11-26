"""Synthetic geometry fixtures; no reference identity or real model invocation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,public_mixed
from backend.app.services.association.mixed_prediction import validate_mixed
from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
from backend.app.services.association.mixed_spatial import spatial_references,validate_spatial_result
from backend.app.services.localization.forward_observation import GeoReference,observe
from backend.tests.model_plot_fixture import plot_answer
from backend.app.api.routers.incremental import commit_model_plot


def spatial_state():
    platform={'name':'A','start_ll':[30.,122.],'heading_deg':0.,'speed_kn':0.,'hull_m':[0.,0.],'tow_m':[-150.,0.]}
    reference=GeoReference(30.,122.);points=[]
    for t in (0.,10.,20.,30.,40.):
        measurement=observe(platform,reference.to_ll((1000+10*t,500+3*t)),t,reference)
        for array in ('hull','tow'):points.append({'candidate_id':array+str(t),'t':t,'array':array,'platform_id':'A','bearing_deg':measurement[array+'_deg']})
    s=create_mixed({'candidates':points,'platform_config':{'platforms':[platform]}})
    s.update(version=2,status='ready',plot_status='awaiting_model',submitted_ids=[p['candidate_id'] for p in points],
        tracks={f'T{i+1:03d}':{'history':[p for p in s['points'] if p['array']==array],'prediction':[],'reason':'独立阵列片段，配对身份尚待验证'} for i,array in enumerate(('hull','tow'))},
        rounds=[{'revision':1,'input':{'step_id':1,'base_state_version':0,'revision_mode':'incremental'},'review_status':'adopted','prediction_generated':False}])
    from backend.app.services.association.joint_identity import pair_evidence
    s['joint_identity_version']=1
    s['targets']={'T001':{'fragment_ids':['F001','F002'],'legacy_track_ids':['T001','T002'],'reason':'合成已采用集合，不是真实模型判断',
                         'review_status':'adopted','identity_confirmed':False,
                         'evidence':pair_evidence(s['tracks']['T001']['history'],s['tracks']['T002']['history'],s['platform_config'])}}
    return s


class MixedSpatialTests(unittest.TestCase):
    def test_selected_geometry_has_mirror_alternatives_not_multiple_targets(self):
        refs=spatial_references(spatial_state())
        self.assertTrue(refs['candidates']);self.assertEqual(len({r['id'] for r in refs['candidates']}),1)
        self.assertTrue(all(not r['association_confirmed'] for r in refs['candidates']))
    def test_model_spatial_coordinates_reproject_and_persist_old_style_png(self):
        s=spatial_state();ctx,_=prepare_model_plot(s);new=apply_model_plot(s,ctx,json.dumps(plot_answer(ctx)))
        spatial=new['rounds'][-1]['model_plot']['spatial_result'];self.assertEqual(spatial['status'],'spatial_candidates');self.assertTrue(spatial['tracks'])
        self.assertTrue(all(t['rmse_deg']<.01 for t in spatial['tracks']))
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.json';commit_model_plot(path,new)
            self.assertTrue(path.exists());png=next(Path(root).glob('spatial_v*/estimated_track.png'))
            self.assertTrue(png.read_bytes().startswith(b'\x89PNG'));self.assertIn('spatial-image',spatial['image_url'])
    def test_model_cannot_invent_distance_without_matching_geometry(self):
        s=spatial_state();ctx,_=prepare_model_plot(s);r=plot_answer(ctx)
        for tr in r['spatial_result']['tracks']:
            for p in tr['points']:p['x']+=5000
        result=apply_model_plot(s,ctx,json.dumps(r))['rounds'][-1]['model_plot']['spatial_result']
        self.assertFalse(result['tracks']);self.assertTrue(result['validation_errors'])

    def test_one_returned_mirror_is_explicitly_incomplete_not_unique(self):
        s=spatial_state();ctx,_=prepare_model_plot(s);r=plot_answer(ctx)
        r['spatial_result']['tracks']=r['spatial_result']['tracks'][:1]
        spatial=apply_model_plot(s,ctx,json.dumps(r))['rounds'][-1]['model_plot']['spatial_result']
        self.assertTrue(spatial['missing_alternatives']);self.assertFalse(spatial['coverage_complete'])
        self.assertFalse(spatial['unique_solution']);self.assertIn('部分',spatial['reason'])

    def test_time_competition_never_evicts_previously_accepted_measurement(self):
        points=[{'candidate_id':cid,'t':0.,'platform_id':'A','array':'hull','bearing_deg':b} for cid,b in [('old',30.),('new',32.)]]
        s=create_mixed({'candidates':points});s['submitted_ids']=['old']
        s['tracks']={'T001':{'history':[p for p in s['points'] if p['candidate_id']=='old'],'reason':'已确认','prediction':[]}}
        ctx,_=prepare_mixed(s,['new'])
        answer={'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[
            {'track_ref':'T001','observation_ids':[s['aliases']['new']],'reason':'竞争点'}]}
        result=validate_mixed(s,ctx,json.dumps(answer))
        self.assertEqual([p['candidate_id'] for p in result['tracks']['T001']['history']],['old'])
        self.assertEqual(result['unassigned_ids'],['new'])
    def test_absent_spatial_output_never_claims_final_trajectory(self):
        s=spatial_state();ctx,_=prepare_model_plot(s);r=plot_answer(ctx);r.pop('spatial_result')
        new=apply_model_plot(s,ctx,json.dumps(r));self.assertEqual(new['plot_status'],'awaiting_spatial')
        self.assertEqual(new['rounds'][-1]['model_plot']['spatial_result']['status'],'not_returned')
    def test_missing_platform_does_not_create_space_from_bearings(self):
        s=spatial_state();s['platform_config']=None;self.assertFalse(spatial_references(s)['candidates'])
    def test_empty_model_bearing_segment_retains_observations_without_fake_line(self):
        s=spatial_state();ctx,_=prepare_model_plot(s);r=plot_answer(ctx);r['tracks'][0]['segments']=[]
        new=apply_model_plot(s,ctx,json.dumps(r));segments=new['tracks']['T001']['model_segments']
        self.assertEqual(len(segments),5);self.assertTrue(all(not seg['curve'] for seg in segments))
    def test_sparse_fragments_not_counted_as_confirmed_physical_targets(self):
        s=spatial_state();s['tracks']['T003']={'history':[s['points'][0]],'prediction':[],'reason':'单点暂定'}
        view=public_mixed(s);self.assertEqual(view['association_audit']['tentative_fragments'],1)
        self.assertFalse(view['association_audit']['physical_target_count_confirmed'])
    def test_explicit_recheck_can_revisit_submitted_points_without_new_observations(self):
        s=spatial_state();before=copy.deepcopy(s)
        ctx,_=prepare_mixed(s,s['submitted_ids'],recheck=True)
        self.assertEqual(ctx['revision_mode'],'historical_replay');self.assertTrue(ctx['recheck_association'])
        self.assertEqual(len(ctx['observations']),len(s['submitted_ids']));self.assertEqual(s,before)
    def test_association_references_do_not_contain_expected_target_count(self):
        s=spatial_state();s['rounds']=[];s['submitted_ids']=[];s['tracks']={}
        ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points']])
        self.assertTrue(ctx['candidate_continuations']);self.assertEqual(ctx['identity_policy']['target_count'],'unknown')

    def test_large_random_selection_transport_is_lossless_without_raw_prompt_cap(self):
        points=[{'candidate_id':f'p{i}_{j}','t':i*9,'platform_id':'A','array':'hull',
                 'bearing_deg':30+j*20+i*.1,'quality':'fitted'} for i in range(32) for j in range(8)]
        s=create_mixed({'candidates':points})
        ctx,prompt=prepare_mixed(s,[p['candidate_id'] for p in points])
        from backend.app.models.association_wire import transport_prompt
        wire=transport_prompt(prompt)
        self.assertLess(len(wire),len(prompt))
        self.assertEqual(len(ctx['observations']),256)
        from backend.app.models.association_wire import encode_context,decode_context
        self.assertEqual(decode_context(encode_context(ctx)),ctx)

    def test_folded_bearing_turn_is_not_rejected_as_linear_outlier(self):
        points=[{'candidate_id':f'p{i}','t':i*10,'platform_id':'A','array':'tow','bearing_deg':b}
                for i,b in enumerate((150,160,170,180,170))]
        s=create_mixed({'candidates':points});ctx,_=prepare_mixed(s,[p['candidate_id'] for p in points])
        answer={'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':[
            {'track_ref':'N1','observation_ids':[s['aliases'][p['candidate_id']] for p in points],'reason':'折叠边界折返'}]}
        result=validate_mixed(s,ctx,json.dumps(answer))
        self.assertEqual(len(result['tracks']['T001']['history']),5)
        self.assertFalse(result['unassigned_ids'])

    def test_multitime_references_are_order_independent_anonymous_candidates(self):
        s=spatial_state();s['rounds']=[];s['submitted_ids']=[];s['tracks']={}
        # Four timestamps required; numerical suggestions must not invent one.
        ctx,_=prepare_mixed(s,[p['candidate_id'] for p in s['points'] if p['t']<=20.])
        self.assertEqual(ctx['multi_time_references'],[])
        from backend.app.services.association.tracklet_references import tracklet_references
        points=[{'candidate_id':f'p{i}','t':i*10,'platform_id':'A','array':'hull','bearing_deg':30+i} for i in range(8)]
        aliases={p['candidate_id']:f'O{i:06d}' for i,p in enumerate(points)}
        refs=tracklet_references(points,aliases)
        self.assertEqual(refs,tracklet_references(list(reversed(points)),aliases))
        self.assertTrue(refs);self.assertFalse(refs[0]['identity_confirmed'])
        self.assertEqual(set(refs[0]['observation_ids']),set(aliases.values()))

    def test_temporal_geometry_uses_actual_times_and_retains_mirror(self):
        from backend.app.services.association.temporal_geometry import temporal_candidates
        platform={'name':'A','start_ll':[30.,122.],'heading_deg':0.,'speed_kn':0.,'hull_m':[0.,0.],'tow_m':[-150.,0.]}
        reference=GeoReference(30.,122.);pairs=[]
        for t in (0.,41.,120.,205.,300.):
            m=observe(platform,reference.to_ll((1500-2*t,1000+5*t)),t,reference)
            pairs.append({'t':t,'hull_deg':m['hull_deg'],'tow_deg':m['tow_deg'],'hull_error_deg':.15,'tow_error_deg':.15})
        candidates=temporal_candidates(platform,pairs)
        self.assertEqual(len(candidates),2)
        self.assertTrue(all(not c['uncertainty_calibrated'] for c in candidates))
        self.assertTrue(all(c['rmse_deg']<.01 for c in candidates))
        self.assertEqual([p['t'] for p in candidates[0]['points']],[p['t'] for p in pairs])
        direct=next(c for c in candidates if c['handedness']==[1,1])
        self.assertLess(max(((p['x']-(1500-2*p['t']))**2+(p['y']-(1000+5*p['t']))**2)**.5 for p in direct['points']),2.)
        degenerate=[{**p,'hull_deg':0.,'tow_deg':0.} for p in pairs]
        self.assertEqual(temporal_candidates(platform,degenerate),[])
