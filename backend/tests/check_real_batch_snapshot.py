"""Explicit paid test: --real required. Max 56 model requests; no retries.

Uses configured model with supplied ZIP observations, writes an isolated task.
Does not adopt results or touch the user's existing sessions. No truth labels.
"""
import argparse
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from backend.app.services.inputs.observation_import import import_observations
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.core.config import model_settings
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--real',action='store_true');parser.add_argument('--resume');parser.add_argument('--all',action='store_true');args=parser.parse_args()
    if not args.real:raise SystemExit('Real paid calls disabled; pass --real only with explicit user authorization')
    load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False)
    settings=model_settings()
    if not settings.api_key:raise SystemExit('No configured model key; no calls made')
    if args.resume:
        return resume(Path(args.resume),settings)
    root=Path('output/supplied-zip-test/real-batch-'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True)
    inputs=[Path(f'C:/Users/LENOVO/AppData/Local/Temp/btr_target_00{i} (2).zip') for i in (1,2,3)]
    dataset=import_observations([(p.name,p.read_bytes()) for p in inputs]);work=create_mixed(dataset,max_calls=56)
    lookup={p['candidate_id']:p for p in work['points']};groups={}
    for source in work['browse_catalog']:
        for cid in source['candidate_ids']:
            p=lookup[cid];groups.setdefault((source['target_id'],p['platform_id'],p['t']),[]).append(cid)
    ordered=sorted(groups.values(),key=lambda ids:lookup[ids[0]]['t'])
    # First 50 span most of the event-time range. Next batch deliberately late+
    # tail observations, proving middle insertion is allowed by protocol.
    selected=[ordered[round(i*470/49)] for i in range(50)]
    leftovers=[g for g in ordered if g not in selected]
    selected += [leftovers[5],leftovers[20],leftovers[-2],leftovers[-1]]
    if args.all:selected=ordered
    maximum=len(selected)+(len(selected)+49)//50;work['max_calls']=maximum
    spec=seq.preview(work,selected,order='received',call_limit=maximum,model_label=settings.model,
        model_fingerprint=seq.model_reference(settings),question='真实测试：请根据观测和历史判断不同轨迹归属，允许迟到观测插入中段，禁止按来源猜目标。')
    spec.pop('context_digest');spec.pop('model_calls');batch.configure(spec,work,batch.MODE,50,50)
    spec.update(context_digest=seq.digest(spec),model_calls=0)
    path=root/'state.json';run=seq.create_run(path,work,spec,'authorized-real-batch')
    work.update(active_sequential_run=run['id'],version=run['root_version'],status='sequential_prepared');save_state(path,work)
    gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'));start=time.perf_counter()
    for cursor in range(0,len(selected),50):
        print(json.dumps({'phase':'dispatch','cursor':cursor,'model':settings.model,'maximum_total_requests':maximum}),flush=True)
        batch.execute(path,run['id'],cursor,settings,gateway,read,save_state)
        current=seq.read_run(path,run['id']);public=seq.public_run(current)
        (root/f'run-after-{cursor}.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'run-latest.json').write_text(json.dumps(public,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'phase':'result','status':current['status'],'processed':current['cursor'],'calls':current['calls']}),flush=True)
        if current['status'] not in ('paused','completed_awaiting_review'):break
    current=seq.read_run(path,run['id'])
    report={'model':settings.model,'run_id':run['id'],'status':current['status'],'processed_groups':current['cursor'],
        'real_model_requests':current['calls'],'elapsed_seconds':round(time.perf_counter()-start,2),
        'targets':len(current['working']['targets']),'snapshots':len(batch.snapshots(path,run['id'])),
        'unassigned_raw':len(current['working']['unassigned_ids']),'last_error':current.get('last_error'),
        'automatic_retries':0,'production_session_modified':False,'results_adopted':False,
        'workers':[{k:v for k,v in r.items() if k in ('unit_id','status','error','error_type')} for s in current['steps'] for r in s.get('worker_results',[])]}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_path':str(root/'report.json'),**report},ensure_ascii=False),flush=True)


def resume(root,settings):
    path=root/'state.json';read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    state=read(path);rid=state['active_sequential_run'];start=time.perf_counter()
    current=seq.read_run(path,rid)
    if current['status']=='failed':batch.recover_saved_proposals(path,rid,read)
    gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    while True:
        current=seq.read_run(path,rid)
        if current['status'] not in ('paused','prepared'):break
        print(json.dumps({'phase':'authorized_resume','cursor':current['cursor'],'calls_already_made':current['calls']}),flush=True)
        batch.execute(path,rid,current['cursor'],settings,gateway,read,save_state)
        current=seq.read_run(path,rid)
        (root/f'run-resumed-{current["cursor"]}.json').write_text(json.dumps(seq.public_run(current),ensure_ascii=False),encoding='utf-8')
    report={'model':settings.model,'run_id':rid,'status':current['status'],'processed_groups':current['cursor'],
        'real_model_requests':current['calls'],'snapshots':len(batch.snapshots(path,rid)),
        'target_hypotheses':len(current['working']['targets']),'unassigned_raw':len(current['working']['unassigned_ids']),
        'additional_elapsed_seconds':round(time.perf_counter()-start,2),'last_error':current.get('last_error'),
        'automatic_retries':0,'single_group_requests_repeated':0,'results_adopted':False,'production_session_modified':False}
    (root/'resumed-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_path':str(root/'resumed-report.json'),**report},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
