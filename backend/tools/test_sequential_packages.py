"""Actual-package prefix isolation test with an explicit no-op fake model."""
import argparse
import copy
import json
import subprocess
import tempfile
import re
from pathlib import Path
from backend.app.core.paths import PROJECT_ROOT
from backend.app.models.model_settings import ModelSettings
from backend.app.services.inputs.observation_import import import_observations
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import sequential_runner as seq


def run(files,output,scenario='unassigned'):
    data=import_observations([(p.name,p.read_bytes()) for p in files])
    js="""const fs=require('fs'),{sampleSourcePairs}=require('./frontend/point-sampling.js');const d=JSON.parse(fs.readFileSync(0,'utf8'));const p=sampleSourcePairs(d.candidates,20,d.target_catalog,{random:()=>.5});const units=[];for(let i=0;i<p.length;i+=2)units.push(p.slice(i,i+2).map(x=>x.candidate_id));process.stdout.write(JSON.stringify(units));"""
    units=json.loads(subprocess.run(['node','-e',js],input=json.dumps(data),text=True,encoding='utf-8',capture_output=True,check=True,cwd=PROJECT_ROOT).stdout)
    state=create_mixed(data,max_calls=30);contexts=[]
    class Fake:
        def invoke(self,images,prompt,*args):
            context=json.loads(prompt.split('\n',1)[1]);contexts.append(context)
            if scenario=='mixed-stream':
                streams=context['fragment_stream_groups']
                rows=([{'track_ref':'N1','observation_ids':[p['observation_id'] for p in context['observations']],'reason':'deliberate invalid mixed-stream proposal'}]
                      if len(contexts)==1 else [{'track_ref':f'N{i+1}','observation_ids':g['observation_ids'],'reason':'protocol fixture only'} for i,g in enumerate(streams)])
                return {'answer':json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],'tracks':rows})}
            return {'answer':json.dumps({'step_id':context['step_id'],'base_state_version':context['base_state_version'],'tracks':[],
                'unassigned':[p['observation_id'] for p in context['observations']]})}
    with tempfile.TemporaryDirectory(prefix='sequential-package-test-') as root:
        path=Path(root)/'state.json';save_state(path,state);spec=seq.preview(state,units,call_limit=20)
        task=seq.create_run(path,state,spec,'actual-packages-local');reserved=copy.deepcopy(state);reserved.update(active_sequential_run=task['id'],version=task['root_version'],status='sequential_prepared');save_state(path,reserved)
        read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
        settings=ModelSettings('explicit-local-fake','http://invalid.local','not-a-real-key')
        for i in range(20):
            seq.execute_one(path,task['id'],i,settings,Fake(),read,save_state)
            current=seq.read_run(path,task['id']);context=current['steps'][-1]['context']
            allowed={state['aliases'][cid] for u in spec['queue'][:i+1] for cid in u['candidate_ids']}
            def references(value):
                if isinstance(value,str):return {value} if re.fullmatch(r'O[0-9]{1,12}',value) else set()
                if isinstance(value,dict):return set().union(*(references(v) for v in value.values()))
                if isinstance(value,list):return set().union(*(references(v) for v in value))
                return set()
            mentioned=references(context)
            assert mentioned<=allowed
            assert len(context['sequential_step']['new_observation_ids'])==2
            assert read(path)['tracks']==state['tracks'] and read(path)['submitted_ids']==[]
            assert 'btr_target_' not in json.dumps(context)
        assert read(path)['points']==state['points']
        warnings=[w for step in current['steps'] for w in step.get('warnings',[])]
        if scenario=='mixed-stream':
            assert any(w['code']=='mixed_stream_fragment' for w in current['steps'][1]['warnings'])
            assert not current['steps'][1].get('error')
        for track in current['working']['tracks'].values():
            assert len({(p['platform_id'],p['array']) for p in track['history']})==1
        geometry=seq.public_run(current)['working_view']['spatial_preview']
        result={'files':len(files),'raw_points':len(state['points']),'selected_units':20,'selected_observations':40,'validated_steps':current['cursor'],
            'fake_model_calls':len(contexts),'real_model_calls':0,'free_initializations':1,'future_leaks':0,'raw_facts_unchanged':True,
            'confirmed_projection_unchanged_before_review':True,'model_accuracy_evaluated':False,'truth_read':False,
            'scenario':scenario,'mixed_stream_quarantined':sum(w['code']=='mixed_stream_fragment' for w in warnings),
            'continuation_policy':current['spec'].get('continuation_policy'),
            'revision_count':len(current['working'].get('continuation_revisions') or []),
            'local_review_steps':sum(s['context'].get('continuation_policy',{}).get('mode')=='local_review' for s in current['steps']),
            'spatial_status':geometry['status'],'spatial_nominal_points':sum(len(t['points']) for t in geometry['tracks']),
            'spatial_unstable_points':sum(not p['stable'] for t in geometry['tracks'] for p in t['points']),
            'note':'Scripted fake replies test execution and conservative validation only, not model identity accuracy.'}
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('files',nargs='+',type=Path);parser.add_argument('--output',type=Path,default=Path('output/supplied-zip-test/sequential-report.json'));parser.add_argument('--scenario',choices=['unassigned','mixed-stream'],default='unassigned');args=parser.parse_args()
    print(json.dumps(run(args.files,args.output,args.scenario),ensure_ascii=False))
