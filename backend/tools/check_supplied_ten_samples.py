"""Ten supplied triplets: real workbench executor, independent post-inference scoring.

No args: import/geometry diagnostics only. --real: 50 groups/sample, max55 requests
each, total550; unknown requests are not retried. Reference only in evaluate().
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import time
import zipfile

from backend.app.core.config import model_settings
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.sequential_spatial_preview import spatial_preview
from backend.app.services.inputs.observation_import import import_observations
from backend.app.services.localization.spatial_tracks import array_position

TEMP=Path('C:/Users/LENOVO/AppData/Local/Temp')
TRIPLETS=[['btr_target_001 (1).zip','btr_target_002 (1).zip','btr_target_003 (1).zip'],
    ['btr_target_001 (1) (1).zip','btr_target_002 (2) (1).zip','btr_target_003 (3).zip']]
TRIPLETS += [[f'btr_target_00{i} ({3*k+i}).zip' for i in (1,2,3)] for k in range(1,9)]
TRIPLETS[2][2]='btr_target_003 (6) (1).zip'


def prepare(names):
    files=[(name,(TEMP/name).read_bytes()) for name in names]
    dataset=import_observations(files);work=create_mixed(dataset,max_calls=55)
    lookup={p['candidate_id']:p for p in work['points']};groups={}
    for source in work['browse_catalog']:
        for cid in source['candidate_ids']:
            p=lookup[cid];groups.setdefault((source['target_id'],p['platform_id'],p['t']),[]).append(cid)
    paired=[ids for ids in groups.values() if len(ids)==2 and {lookup[c]['array'] for c in ids}=={'hull','tow'}]
    paired.sort(key=lambda ids:(lookup[ids[0]]['t'],seq.digest(sorted(ids))))
    selected=[paired[round(i*(len(paired)-1)/49)] for i in range(50)] if len(paired)>50 else paired
    units=[{'unit_id':f'U{i+1:06d}','candidate_ids':ids,'validated':True} for i,ids in enumerate(selected)]
    geometry=spatial_preview({'points':work['points'],'units':units,'membership':{},'continuation_graph':{}},work['platform_config'])
    return work,selected,geometry,{'archives':names,'sha256':[hashlib.sha256(raw).hexdigest() for _,raw in files],
        'raw_observations':len(work['points']),'available_paired_groups':len(paired),'sampled_groups':len(selected),
        'platform':work['platform_config'],'sample_time_span':[lookup[selected[0][0]]['t'],lookup[selected[-1][0]]['t']],
        'sample_groups_with_geometry':len({p['unit_id'] for tr in geometry['tracks'] for p in tr['points']}),
        'sample_unavailable_geometry':geometry['unavailable']}


def evaluate(root,run,names):
    # Only post-inference metrics/plots use reference/source identity.
    view=seq.public_run(run)['working_view'];work=run['working'];origin=view['spatial_preview']['origin_lat_lon']
    truth={};lookup={p['candidate_id']:p for p in work['points']}
    for index,name in enumerate(names,1):
        with zipfile.ZipFile(TEMP/name) as archive:ref=json.loads(archive.read('evaluation/reference.json'))
        truth[f'I{index:02d}']={q['t']:(6371000*math.cos(math.radians(origin[0]))*math.radians(q['longitude']-origin[1]),
            6371000*math.radians(q['latitude']-origin[0])) for q in ref['points']}
    candidates=collections.defaultdict(list)
    for tr in view['spatial_preview']['tracks']:
        branch=''.join('p' if h==1 else 'm' for h in tr['handedness'])
        for p in tr['points']:candidates[p['unit_id']].append({**p,'branch_ref':branch})
    counts=collections.defaultdict(collections.Counter);paths=collections.defaultdict(list);errors=[];oracle=[];mirror_sums=collections.defaultdict(lambda:[0.,0.,0])
    assignments=work.get('batch_assignments',{});branches=work.get('batch_position_choices',{})
    labelled=[];chosen_points=[]
    for u in run['spec']['queue'][:run['cursor']]:
        cid=u['candidate_ids'][0];source=cid.split('-')[0];target=assignments.get(cid);counts[str(target)][source]+=1
        labelled.append((source,target));ref=truth[source].get(lookup[cid]['t'])
        if ref is None:continue
        ps=candidates[u['unit_id']]
        if ps:oracle.append(min(math.dist((p['x'],p['y']),ref) for p in ps))
        q=next((p for p in ps if p['branch_ref']==branches.get(cid)),None)
        if not q:continue
        error=math.dist((q['x'],q['y']),ref);errors.append(error);chosen_points.append(q)
        if target:paths[target].append({**q,'source':source})
        nav=next(p for p in work['platform_config']['platforms'] if p['name']==lookup[cid]['platform_id'])
        h=array_position(nav,nav['hull_m'],q['t'],origin);t=array_position(nav,nav['tow_m'],q['t'],origin)
        dx,dy=t[0]-h[0],t[1]-h[1];span=math.hypot(dx,dy);dx/=span;dy/=span
        qx,qy=q['x']-h[0],q['y']-h[1];dot=qx*dx+qy*dy;mirror=(h[0]+2*dot*dx-qx,h[1]+2*dot*dy-qy)
        sums=mirror_sums[target or u['unit_id']];sums[0]+=error**2;sums[1]+=math.dist(mirror,ref)**2;sums[2]+=1
    pair_tp=pair_fp=pair_fn=0
    for i,(sa,ta) in enumerate(labelled):
        for sb,tb in labelled[i+1:]:
            same_source=sa==sb;same_target=ta is not None and ta==tb
            pair_tp+=same_source and same_target;pair_fp+=not same_source and same_target;pair_fn+=same_source and not same_target
    edges=wrong=0;maximum_speed=0
    for path in paths.values():
        path.sort(key=lambda p:p['t'])
        for a,b in zip(path,path[1:]):
            if b['t']<=a['t']:continue
            edges+=1;wrong+=a['source']!=b['source'];maximum_speed=max(maximum_speed,math.dist((a['x'],a['y']),(b['x'],b['y']))/(b['t']-a['t']))
    rmse=lambda es:math.sqrt(sum(e*e for e in es)/len(es)) if es else None
    report={'processed_groups':run['cursor'],'selected_positions':len(errors),'unassigned_groups':sum(t is None for _,t in labelled),
        'target_hypotheses':len(paths),'source_confusion_evaluation_only':dict(counts),'cross_source_edges':wrong,'edges':edges,
        'pairwise_precision':pair_tp/(pair_tp+pair_fp) if pair_tp+pair_fp else None,
        'pairwise_recall':pair_tp/(pair_tp+pair_fn) if pair_tp+pair_fn else None,
        'selected_rmse_m':rmse(errors),'mirror_aligned_rmse_m_evaluation_only':math.sqrt(sum(min(v[:2]) for v in mirror_sums.values())/len(errors)) if errors else None,
        'reference_nearest_candidate_rmse_m_evaluation_only':rmse(oracle),'max_apparent_speed_mps':maximum_speed,
        'reference_used_for_inference':False,'mirror_unique':False,'status':run['status'],'real_requests':run['calls'],'last_error':run.get('last_error')}
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5),facecolor='#0b1020')
    for ax in axes:
        ax.set_facecolor('#0b1020');ax.tick_params(colors='#bfcedd');ax.grid(alpha=.15);ax.set_aspect('equal',adjustable='datalim')
        ax.set_xlabel('East (m)',color='white');ax.set_ylabel('North (m)',color='white')
    for source,path in truth.items():
        ps=list(path.values());axes[0].plot([p[0] for p in ps],[p[1] for p in ps],label=source)
    for target,path in paths.items():axes[1].plot([p['x'] for p in path],[p['y'] for p in path],marker='.',markersize=3,label=target)
    for p in view['spatial_preview']['platforms']:
        ps=p['points'];axes[1].plot([p['x'] for p in ps],[p['y'] for p in ps],color='yellow',label='platform')
    axes[0].set_title('Reference (evaluation ONLY)',color='white');axes[1].set_title('Real model work hypotheses (mirrors unresolved)',color='white')
    for ax in axes:
        if ax.get_legend_handles_labels()[0]:ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(root/'comparison.png',dpi=120);plt.close(fig)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--real',action='store_true');parser.add_argument('--root',type=Path)
    args=parser.parse_args();root=args.root or Path('output/supplied-zip-test')/('ten-sample-'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True,exist_ok=True)
    rows=[];prepared=[]
    for index,names in enumerate(TRIPLETS,1):
        work,selected,geometry,metadata=prepare(names);metadata['sample']=index;prepared.append((work,selected,metadata));rows.append(metadata)
    (root/'inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'root':str(root),'phase':'inventory','samples':len(rows),'raw_counts':[r['raw_observations'] for r in rows],
        'platform_speed_kn':[r['platform']['platforms'][0]['speed_kn'] for r in rows],'geometry_counts':[r['sample_groups_with_geometry'] for r in rows]}),flush=True)
    if not args.real:return
    settings=model_settings()
    if not settings.api_key:raise ValueError('No configured model; no calls made')
    results=[];gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    for index,(work,selected,metadata) in enumerate(prepared,1):
        case=root/f'sample-{index:02d}';case.mkdir(exist_ok=False);path=case/'state.json'
        spec=seq.preview(work,selected,order='received',call_limit=55,model_fingerprint=seq.model_reference(settings),model_label=settings.model)
        spec.pop('context_digest');spec.pop('model_calls');batch.configure(spec,work,batch.MODE,50,50,2,4,True,True)
        spec.update(context_digest=seq.digest(spec),model_calls=0);run=seq.create_run(path,work,spec,'explicit-ten-sample-test')
        work.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(path,work)
        print(json.dumps({'phase':'real_dispatch','sample':index,'model':settings.model,'maximum_requests':55}),flush=True);start=time.perf_counter()
        batch.execute(path,run['id'],0,settings,gateway,lambda p:json.loads(Path(p).read_text(encoding='utf-8')),save_state)
        result=seq.read_run(path,run['id']);public=seq.public_run(result)
        (case/'public-run.json').write_text(json.dumps(public,ensure_ascii=False),encoding='utf-8')
        report=evaluate(case,result,metadata['archives']);report.update(sample=index,elapsed_seconds=round(time.perf_counter()-start,2),sampled_groups=len(selected),
            actual_workbench_executor=True,production_session_modified=False,model=settings.model)
        (case/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');results.append(report)
        (root/'reports.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True),flush=True)
    print(json.dumps({'phase':'done','samples':len(results),'real_requests':sum(r['real_requests'] for r in results),'root':str(root)}),flush=True)


if __name__=='__main__':main()
