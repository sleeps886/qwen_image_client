"""Local-only tests of supplied packages. No gateway or evaluation truth reads."""
import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from backend.app.core.paths import PROJECT_ROOT
from backend.app.services.inputs.observation_import import import_observations
from backend.app.services.inputs.observation_diagnostics import observation_diagnostics
from backend.app.services.inputs.workspace_observations import enable
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed
from backend.app.models.association_wire import encode_context,decode_context,transport_prompt


def measurements(rows):
    return {(p['candidate_id'],p['t'],p['platform_id'],p['array'],p['bearing_deg']) for p in rows}


def run(paths,output):
    files=[(f'btr_target_{i:03d}.zip',path.read_bytes()) for i,path in enumerate(paths,1)]
    report={'format':'supplied-package-test-v1','files':[], 'real_model_calls':0,
            'truth_read':False,'model_accuracy_evaluated':False,'cases':[]}
    for (name,raw),path in zip(files,paths):
        data=import_observations([(name,raw)])
        report['files'].append({'file':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                                'observations':observation_diagnostics(data['candidates'])})
    merged=import_observations(files)
    report['merged']=observation_diagnostics(merged['candidates'])
    report['merged']['browse_groups']=len(merged['target_catalog'])
    report['merged']['unique_ids']=len({p['candidate_id'] for p in merged['candidates']})
    sampling=r'''
const fs=require('fs'),{sampleOrdered,sampleSynchronousGroups}=require('./frontend/point-sampling.js'),{propose}=require('./frontend/evidence-neighborhood.js');
const data=JSON.parse(fs.readFileSync(0,'utf8')),points=data.candidates.sort((a,b)=>a.t-b.t||a.platform_id.localeCompare(b.platform_id)||a.array.localeCompare(b.array)||a.candidate_id.localeCompare(b.candidate_id));
const rng=seed=>()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
const cases=[7,19,37,73,101].map(seed=>({name:'points_20_seed_'+seed,selected_ids:sampleOrdered(points,20,{random:rng(seed)}).map(p=>p.candidate_id)}));
cases.push({name:'synchronous_groups_20_seed_19',selected_ids:sampleSynchronousGroups(points,20,{random:rng(19)}).map(p=>p.candidate_id)});
const ids=new Set(cases[1].selected_ids),neighbors=propose(points,points.filter(p=>ids.has(p.candidate_id)));
cases.push({name:'optional_neighborhood_seed_19',selected_ids:[...new Set([...ids,...neighbors])]});
cases.push({name:'all_original_points',selected_ids:points.map(p=>p.candidate_id)});
process.stdout.write(JSON.stringify(cases));
'''
    cases=json.loads(subprocess.run(['node','-e',sampling],input=json.dumps(merged),capture_output=True,text=True,encoding='utf-8',cwd=PROJECT_ROOT,check=True).stdout)
    # If an existing run used exactly these facts, repeat its selected scope for
    # local diagnostics only. Never read its model body as truth or modify it.
    original=measurements(merged['candidates'])
    for path in sorted((PROJECT_ROOT/'outputs/mixed_prediction').glob('*/state.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:15]:
        old=json.loads(path.read_text(encoding='utf-8'))
        if measurements(old.get('points',[]))==original and len(old.get('submitted_ids',[]))==20:
            cases.append({'name':'existing_20_point_scope','selected_ids':old['submitted_ids']})
            report['existing_scope_source']={'session_id':path.parent.name,'submitted':20,'assigned':sum(len(t['history']) for t in old.get('tracks',{}).values()),'unassigned':len(old.get('unassigned_ids',[]))}
            break
    output.mkdir(parents=True,exist_ok=True)
    (output/'observations-merged.json').write_text(json.dumps(merged,ensure_ascii=False),encoding='utf-8')
    (output/'selection-cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    before=measurements(merged['candidates'])
    with tempfile.TemporaryDirectory(prefix='supplied-zip-test-') as root:
        directory=Path(root)
        state=enable(directory/'state.json',create_mixed(merged),'supplied-package-test-clock')
        lookup={p['candidate_id']:p for p in state['points']}
        for case in cases:
            started=time.perf_counter();context,prompt=prepare_mixed(state,case['selected_ids'])
            selected=[lookup[cid] for cid in case['selected_ids']]
            stats=observation_diagnostics(selected)
            assert len(context['observations'])==len(selected)
            assert decode_context(encode_context(context))==context
            encoded=json.dumps(context,ensure_ascii=False)
            assert 'btr_target_' not in encoded and 'evaluation/reference.json' not in encoded
            report['cases'].append({'name':case['name'],'selected':stats,'reference_count':len(context['multi_time_references']),
                'reference_covered_points':context['candidate_coverage']['covered_points'],
                'references_by_array':{a:sum(r['array']==a for r in context['multi_time_references']) for a in ('hull','tow')},
                'reference_pairings':len(context['joint_identity']['pairings']),
                'numerically_supported_pairings':sum(p['eligible_for_spatial'] for p in context['joint_identity']['pairings']),
                'pairing_shortlist_truncated':context['joint_identity']['shortlist_truncated'],
                'wire_chars':len(transport_prompt(prompt)),'seconds':round(time.perf_counter()-started,3),
                'lossless_roundtrip':True,'source_labels_excluded':True,'model_calls':0})
            print(case['name'], 'points='+str(len(selected)), 'sync='+str(stats['strict_dual_group_count']),
                  'reference_covered='+str(context['candidate_coverage']['covered_points']),flush=True)
    assert measurements(merged['candidates'])==before
    report['raw_measurements_unchanged']=True
    report['note']='数值参考覆盖/配对不是模型归属或真实目标数；未执行真实模型关联、绘图或准确率评估。'
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('files',nargs='+',type=Path);parser.add_argument('--output',type=Path,default=Path('output/supplied-zip-test'));args=parser.parse_args()
    result=run(args.files,args.output)
    print('PASS:',result['merged']['point_count'],'merged points;',len(result['cases']),'scopes; 0 real model calls')
