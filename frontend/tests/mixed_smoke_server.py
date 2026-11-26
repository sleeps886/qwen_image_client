"""Explicit fake-gateway UI server. No credentials and no external model requests."""
import json
import asyncio
from backend.tests.model_plot_fixture import plot_answer
import tempfile
import time
from pathlib import Path
import uvicorn
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings

class FakeGateway:
    async def stream(self,images,prompt,settings,output_root,mode):
        result=await asyncio.to_thread(self.invoke,images,prompt,settings,output_root,mode)
        for i in range(0,len(result['answer']),48):
            yield {'event':'delta','text':result['answer'][i:i+48]}
            await asyncio.sleep(.04)
        yield {'event':'model_complete','result':result}
    def invoke(self,images,prompt,settings,output_root,mode):
        time.sleep(.5)
        ctx=json.loads(prompt.split('\n',1)[1]);bins={}
        compact=ctx.get('wire_format')=='batch-compact-v1'
        if compact:
            from backend.app.services.association.batch_protocol import decode
            ctx=decode(ctx)
        if ctx.get('wire_format')=='spatial-cards-lossless-v1':
            from backend.app.services.association.spatial_card_protocol import decode_frame
            ctx=decode_frame(ctx)
        if ctx.get('protocol')=='model-selected-spatial-cards-v1':
            if 'focus_unit_id' in ctx:
                result={'snapshot_version':ctx['snapshot_version'],'hypothesis_ref':next(iter(ctx['allowed_hypothesis_refs']),None),'reason_code':'continuity'}
            elif not ctx['partition_proposals']:
                result={'snapshot_version':ctx['snapshot_version'],'reject_all':True}
            else:
                plan=ctx['partition_proposals'][0]
                result={'snapshot_version':ctx['snapshot_version'],'partition_ref':plan['partition_ref'],'chain_bindings':[
                    {'chain_ref':c['chain_ref'],'target_ref':max(c['provisional_history_target_support'],key=c['provisional_history_target_support'].get)
                     if c['provisional_history_target_support'] else f'N{i+1}'} for i,c in enumerate(plan['chains'])]}
            return {'answer':json.dumps(result),'usage':{'fake_gateway':True}}
        if ctx.get('question')=='TEST_ONLY_BAD_POINT_INDEX':
            return {'answer':json.dumps({'format':'association-compact-v1','step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                'tracks':[{'track_ref':'N1','point_indices':[99999],'reason_code':'continuity'}]}),'usage':{'fake_gateway':True}}
        if ctx.get('protocol')=='batch-snapshot-v1':
            if 'retry_feedback' not in ctx and (
                ctx.get('question')=='TEST_RETRY_BAD_WORKER' and ctx.get('focus_unit_id')=='U000001' or
                ctx.get('question')=='TEST_RETRY_BAD_MERGE' and 'focus_unit_id' not in ctx):
                return {'answer':'{"reason":"deliberately broken JSON'}
            reason={'reason_code':'continuity'} if compact else {'reason':'isolated fake judgement'}
            if 'focus_unit_id' in ctx:
                result={'snapshot_version':ctx['snapshot_version'],'unit_id':ctx['focus_unit_id'],
                        'target_ref':next(iter(ctx['snapshot']['targets']),'new'),**reason}
            else:
                result={'snapshot_version':ctx['snapshot_version'],'assignments':[
                    {'unit_id':u['unit_id'],'target_ref':next(iter(ctx['snapshot']['targets']),'N1'),**reason} for u in ctx['batch_units']],'revisions':[]}
            return {'answer':json.dumps(result),'usage':{'fake_gateway':True}}
        if ctx.get('question')=='TEST_ONLY_UNKNOWN_CANDIDATE':
            rows=[{'track_ref':'N1','candidate_ref':'R999999','reason':'deliberately nonexistent candidate'}] if ctx['step_id']==4 else []
            return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                'tracks':rows,'unassigned':[p['observation_id'] for p in ctx['observations']]}),'usage':{'fake_gateway':True}}
        if ctx.get('question')=='TEST_ONLY_TENTATIVE_REVIEW':
            rows=[]
            for i,array in enumerate(('hull','tow'),1):
                ps=[p for p in ctx['observations'] if p['array']==array]
                if ctx['step_id']==2:
                    rows.append({'track_ref':f'F00{i}','observation_ids':[p['observation_id'] for p in ps],'reason':'fixture sparse append'})
                else:
                    rows.extend([{'track_ref':f'F00{i}','observation_ids':[p['observation_id'] for p in ps if p['t']==0],'reason':'fixture keep first'},
                                 {'track_ref':f'N{i}','observation_ids':[p['observation_id'] for p in ps if p['t']>0],'reason':'fixture later evidence splits early edge'}])
            return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':rows}),'usage':{'fake_gateway':True}}
        if ctx.get('task')=='model_bearing_plot':return {'answer':json.dumps(plot_answer(ctx)), 'usage':{'fake_gateway':True}}
        supported=next((p for p in ctx.get('joint_identity',{}).get('pairings',[]) if p['eligible_for_spatial']),None)
        if supported:
            return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                'tracks':[{'track_ref':f'N{i+1}','candidate_ref':ref,'reason':'隔离测试网关引用数值候选，非真实模型效果'} for i,ref in enumerate(supported['fragment_refs'])],
                'target_hypotheses':[{'target_ref':'H1','fragment_refs':['N1','N2'],'pairing_ref':supported['reference_id'],'reason':'隔离测试的联合身份假设'}]}),'usage':{'fake_gateway':True}}
        for p in ctx['observations']:
            # Test fixture has clearly separated angular clusters, not source-based IDs.
            key=(p['platform_id'],p['array'],p['bearing_deg']>70)
            bins.setdefault(key,[]).append(p)
        tracks=[]
        for i,((pid,array,side),ps) in enumerate(sorted(bins.items())):
            ref=next((tid for tid,t in ctx['tracks'].items() if (old:=t['history'] or ctx.get('previous_association_for_review',{}).get(tid,[])) and old[-1]['platform_id']==pid and old[-1]['array']==array and (old[-1]['bearing_deg']>70)==side),f'N{i+1}')
            tracks.append({'track_ref':ref,'observation_ids':[p['observation_id'] for p in ps],'reason':'模拟网关：按时间、阵列和方位连续性建立测试假设，不是模型效果验证'})
        return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],'tracks':tracks,'unassigned':[],'ambiguous':[]}), 'usage':{'total_tokens':0,'fake_gateway':True}}

@app.get('/api/test-fake-gateway')
def marker():return {'fake_gateway':True}


@app.get('/api/test-saved-display/{kind}')
def saved_display_fixture(kind:str):
    if kind not in ('failed','complete'):raise ValueError('Unknown isolated fixture')
    path=Path('output/supplied-zip-test/current-incomplete-display')/(kind+'-fixture.json')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/api/test-sequential-spatial-dataset')
def sequential_spatial_dataset():
    from backend.tests.test_mixed_spatial import spatial_state
    s=spatial_state()
    return {'candidates':[{**p,'sigma_deg':.2} for p in s['points']],
            'platform_config':s['platform_config'],'fake_gateway':True}


@app.get('/api/test-empty-spatial-fixture')
def empty_spatial_fixture(empty:bool=True,fresh:bool=False):
    from backend.tests.test_sparse_plot_diagnostics import fixture
    from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
    from backend.app.services.inputs.workspace_observations import enable
    s=fixture();directory=api.ResultRepository(api.MIXED_ROOT).create_run();path=directory/'state.json'
    s=enable(path,s,'sparse-fixture-clock');ctx,_=prepare_model_plot(s)
    if fresh:
        s['calls']=1;api.save_state(path,s)
        return {'session_id':directory.name,'dataset':{'candidates':s['points']},'fake_gateway':True}
    reply=plot_answer(ctx)
    if empty:
        for track in reply['tracks']:
            track.update(decision='insufficient_evidence',prediction=[],segments=[])
        reply['review_requests']=[{'fragment_id':'F003','reason':'隔离测试：稀疏支持待复核'}]
    s=apply_model_plot(s,ctx,json.dumps(reply));s['calls']=2;api.save_state(path,s)
    return {'session_id':directory.name,'dataset':{'candidates':s['points']},'fake_gateway':True}

@app.get('/api/test-mixed-fixture')
def fixture():
    points=[{'candidate_id':f'{label}-{array}-{i}','platform_id':'P','array':array,'t':i*10,'bearing_deg':base+i,'quality':'fitted','mixed':False,'sigma_deg':1.}
            for label,base in [('A038',30),('A031',110)] for array in ('hull','tow') for i in range(5)]
    return {'candidates':points,'target_catalog':[{'target_id':label,'candidate_ids':[p['candidate_id'] for p in points if p['candidate_id'].startswith(label)]} for label in ('A038','A031')]}

@app.get('/api/test-spatial-fixture')
def spatial_fixture():
    from backend.tests.test_mixed_spatial import spatial_state
    from backend.app.services.association.model_plot import prepare_model_plot,apply_model_plot
    state=spatial_state();ctx,_=prepare_model_plot(state);result=apply_model_plot(state,ctx,json.dumps(plot_answer(ctx)))
    directory=api.ResultRepository(api.MIXED_ROOT).create_run();api.commit_model_plot(directory/'state.json',result)
    return {'session_id':directory.name,'fake_gateway':True}


@app.get('/api/test-joint-dataset')
def joint_dataset():
    from backend.tests.test_joint_identity import joint_fixture
    state,_=joint_fixture()
    return {'candidates':state['points'],'platform_config':state['platform_config'],'target_catalog':state['browse_catalog']}


@app.get('/api/test-legacy-fragment-review')
def legacy_fragment_review():
    """Saved old failure fixture; all model content comes from the fake fixture."""
    from backend.tests.test_mixed_prediction import dataset,apply
    from backend.app.services.association.mixed_prediction import create_mixed,review_mixed
    from backend.app.services.association.model_plot import prepare_model_plot,PLOT_NODES
    s=apply(create_mixed(dataset()),['A038_0','A038_1'],[('N1',['A038_0','A038_1'])])
    s=review_mixed(s,s['version'],True);ctx,_=prepare_model_plot(s);reply=plot_answer(ctx)
    reply['review_requests']=[{'target_id':'T001','reason':'旧格式实际请求复核单阵片段'}]
    s.update(status='plot_invalid_response',calls=2)
    directory=api.ResultRepository(api.MIXED_ROOT).create_run();api.save_state(directory/'state.json',s)
    api.save_state(directory/'plot_attempt_002.json',{'status':'returned','input':ctx,'answer':json.dumps(reply)})
    trace=api.PredictionExecution(directory,s['version'],nodes=PLOT_NODES)
    try:
        with trace.node('plot_validation'):
            error=ValueError('关联复核请求引用不存在或重复的目标')
            api.validation_detail(trace,'plot_validation',error);raise error
    except ValueError:pass
    return {'session_id':directory.name,'fake_gateway':True}


@app.get('/api/test-rerun-fixture')
def rerun_fixture(phase:str='association',unknown:bool=False):
    from backend.tests.test_manual_rerun import association_failure,plot_failure
    s,ctx=(plot_failure(unknown) if phase=='plot' else association_failure(unknown))
    directory=api.ResultRepository(api.MIXED_ROOT).create_run();api.save_state(directory/'state.json',s)
    prefix='plot_attempt' if phase=='plot' else 'attempt'
    api.save_state(directory/f"{prefix}_{s['calls']:03d}.json",{'status':'call_unknown' if unknown else 'returned','input':ctx,'answer':'invalid fake reply, retained for retry audit'})
    return {'session_id':directory.name,'fake_gateway':True}

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='mixed-ui-') as root:
        api.MIXED_ROOT=Path(root)
        from backend.app.api.routers import live_tracking as live_api
        live_api.LIVE_DB=Path(root)/'isolated-live.sqlite3'
        api.model_settings=lambda:ModelSettings(model='fake',base_url='http://invalid.local',api_key='fake-not-a-secret')
        app.dependency_overrides[api.get_prediction_gateway]=FakeGateway
        uvicorn.run(app,host='127.0.0.1',port=8891)
