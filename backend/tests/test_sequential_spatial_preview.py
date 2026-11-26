import copy
from backend.tests.test_mixed_spatial import spatial_state
from backend.app.services.association.sequential_spatial_preview import spatial_preview


def fixture():
    s=spatial_state();units=[];membership={};edges=[]
    times=sorted({p['t'] for p in s['points']})
    for i,t in enumerate(times):
        ps=[p for p in s['points'] if p['t']==t]
        units.append({'unit_id':f'U{i}','candidate_ids':[p['candidate_id'] for p in ps],'validated':True})
    for tid,tr in s['tracks'].items():
        for p in tr['history']:membership[p['candidate_id']]={'status':'associated','fragment_id':'F'+tid[1:],'target_id':None}
        for a,b in zip(tr['history'],tr['history'][1:]):edges.append({'from_id':a['candidate_id'],'to_id':b['candidate_id'],'status':'low_confidence'})
    return {'points':s['points'],'units':units,'membership':membership,'continuation_graph':{'edges':edges}},s['platform_config']


def test_single_unit_has_conditional_space_point_and_mirror_not_fake_line():
    view,config=fixture();view['units']=view['units'][:1];before=copy.deepcopy(view)
    doc=spatial_preview(view,config)
    assert doc['coordinate_system']=='local_xy_m' and not doc['unique_solution']
    assert len(doc['tracks'])>=2
    assert all(len(t['points'])==1 and not t['points'][0]['connect_previous'] for t in doc['tracks'])
    for tr in doc['tracks']:
        p=tr['points'][0]
        assert abs(abs(p['x'])-1000)<1 and abs(p['y']-500)<1
    assert view==before


def test_accumulated_geometry_uses_both_arrays_and_checked_edges():
    view,config=fixture();doc=spatial_preview(view,config)
    assert doc['tracks']
    for tr in doc['tracks']:
        assert len(tr['points'])==5
        assert all(p['connect_previous'] for p in tr['points'][1:])
        for p in tr['points']:
            assert abs(abs(p['x'])-(1000+10*p['t']))<1
            assert abs(p['y']-(500+3*p['t']))<1
    assert doc['platforms'][0]['points'][0]['x']==0


def test_missing_platform_or_single_array_never_supplies_range():
    view,config=fixture();missing=spatial_preview(view,None);assert not missing['tracks']
    assert {u['unit_id'] for u in missing['unavailable']}=={u['unit_id'] for u in view['units']}
    view['units'][0]['candidate_ids']=view['units'][0]['candidate_ids'][:1];view['units']=view['units'][:1]
    doc=spatial_preview(view,config)
    assert not doc['tracks'] and doc['unavailable']


def test_unassigned_or_unvalidated_cannot_be_connected():
    view,config=fixture()
    for m in view['membership'].values():m['status']='unassigned'
    doc=spatial_preview(view,config)
    assert doc['tracks'] and not any(p['connect_previous'] for tr in doc['tracks'] for p in tr['points'])
    view,config=fixture();view['units'][1]['validated']=False
    doc=spatial_preview(view,config)
    for tr in doc['tracks']:
        assert not any(p['connect_previous'] for p in tr['points'] if p['t'] in (10.,20.))


def test_bad_geometry_and_high_sigma_are_explicit_not_reliable_lines():
    view,config=fixture()
    for p in view['points']:p['bearing_error_assumption_deg']=30.
    doc=spatial_preview(view,config)
    assert doc['tracks'] and any(not p['stable'] for tr in doc['tracks'] for p in tr['points'])
    assert not any(p['connect_previous'] for tr in doc['tracks'] for p in tr['points'])
    config['platforms'][0]['tow_m']=[0.,0.]
    doc=spatial_preview(view,config)
    assert not doc['tracks'] and all('基线' in u['reason'] for u in doc['unavailable'])


def test_peak_width_is_not_treated_as_calibrated_bearing_error():
    view,config=fixture()
    before=spatial_preview(view,config)
    for p in view['points']:p['sigma_deg']=30.
    after=spatial_preview(view,config)
    assert before==after


def test_different_target_identities_do_not_intersect_as_one_target():
    view,config=fixture()
    for p in view['points']:view['membership'][p['candidate_id']]['target_id']='T1' if p['array']=='hull' else 'T2'
    assert not spatial_preview(view,config)['tracks']


def test_absent_units_and_missing_edges_are_not_bridged():
    view,config=fixture();view['units']=[view['units'][0],view['units'][2]]
    doc=spatial_preview(view,config)
    assert {p['t'] for tr in doc['tracks'] for p in tr['points']}=={0.,20.}
    assert not any(p['connect_previous'] for tr in doc['tracks'] for p in tr['points'])


def test_heading_speed_and_array_offsets_affect_geometry_not_target_truth():
    from backend.app.services.localization.forward_observation import GeoReference,observe
    view,config=fixture();platform=config['platforms'][0]
    platform.update(heading_deg=30.,speed_kn=10.,hull_m=[30.,20.],tow_m=[-120.,20.])
    ref=GeoReference(*platform['start_ll'])
    for p in view['points']:
        measured=observe(platform,ref.to_ll((2000+5*p['t'],500+3*p['t'])),p['t'],ref)
        p['bearing_deg']=measured[p['array']+'_deg']
    doc=spatial_preview(view,config)
    true_side=next(t for t in doc['tracks'] if t['handedness']==[1,1])
    for p in true_side['points']:
        assert abs(p['x']-(2000+5*p['t']))<1
        assert abs(p['y']-(500+3*p['t']))<1
    assert doc['platforms'][0]['points'][-1]['x']>90
