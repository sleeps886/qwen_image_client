"""Reproducible mixed real-data replay. --run-model explicitly permits two calls.

Directory/offline memberships and reference XY are scorer-only, never model input.
No automatic retry; a returned invalid reply is retained for local diagnosis.
"""
import argparse
import json
import random
import math
from pathlib import Path

from backend.app.main import app  # loads the project's configured environment
from backend.app.api.routers import incremental as api
from backend.app.services.association.mixed_prediction import (
    create_mixed, prepare_mixed, validate_mixed, update_mixed, review_mixed)
from backend.app.services.association.model_plot import prepare_model_plot, apply_model_plot


def select_rows(batch, seed, count=12):
    rng=random.Random(seed)
    times=sorted({p['t'] for p in batch['candidates']})
    # Stratified random times, all candidates at each time; no identity filtering.
    chosen=[rng.choice(times[i*len(times)//count:(i+1)*len(times)//count]) for i in range(count)]
    ids=[p['candidate_id'] for p in batch['candidates'] if p['t'] in chosen]
    rng.shuffle(ids)
    return ids


def association_score(state,batch):
    memberships={cid:r['target_id'] for r in batch.get('target_catalog',[]) for cid in r['candidate_ids']}
    assigned={p['candidate_id']:tid for tid,tr in state['tracks'].items() for p in tr['history']}
    selected=[p for p in state['points'] if p['candidate_id'] in state['submitted_ids'] and p['candidate_id'] in memberships]
    tp=fp=fn=0
    for i,a in enumerate(selected):
        for b in selected[i+1:]:
            if (a['platform_id'],a['array'])!=(b['platform_id'],b['array']):continue
            truth=memberships[a['candidate_id']]==memberships[b['candidate_id']]
            predicted=a['candidate_id'] in assigned and b['candidate_id'] in assigned and assigned[a['candidate_id']]==assigned[b['candidate_id']]
            tp+=truth and predicted;fp+=not truth and predicted;fn+=truth and not predicted
    return {'reference_is_offline_hypothesis_not_truth':True,'pair_precision':tp/(tp+fp) if tp+fp else None,
            'pair_recall':tp/(tp+fn) if tp+fn else None,'scored_selected_points':len(selected),
            'fragments':len(state['tracks']),'unassigned':len(state['unassigned_ids'])}


def compare_space(spatial,reference_file,output):
    """Offline visual/numerical diagnostic, NEVER an independent truth score."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    reference=json.loads(reference_file.read_text(encoding='utf-8'))
    if reference.get('coordinate_system')!='local_xy_m':
        return {'comparable':False,'reason':'reference coordinates not local_xy_m'}
    # Same representative-view policy as the old plot, not both mirror views
    # superposed into a different-looking comparison picture.
    if reference.get('display_view')=='model_primary_hypotheses':
        reference['tracks']=[tr for tr in reference['tracks'] if tr.get('mirror_variant',0)==0]
    def interpolate(points,t):
        for a,b in zip(points,points[1:]):
            if a['t']<=t<=b['t']:
                r=(t-a['t'])/(b['t']-a['t'])
                return a['x']+r*(b['x']-a['x']),a['y']+r*(b['y']-a['y'])
        return None
    fig,axes=plt.subplots(1,2,figsize=(14,6),sharex=True,sharey=True)
    scored=[]
    colors={'N1':'orange','N2':'magenta','N3':'cyan','N4':'chartreuse'}
    for tr in reference['tracks']:
        pts=tr['points'];axes[0].plot([p['x'] for p in pts],[p['y'] for p in pts],label=tr['id'],color=colors.get(tr['id']))
    for tr in spatial['tracks']:
        pts=tr['points'];axes[1].plot([p['x'] for p in pts],[p['y'] for p in pts],'.-',label=tr['id'])
        matches=[]
        for ref in reference['tracks']:
            distances=[math.dist((p['x'],p['y']),v) for p in pts if (v:=interpolate(ref['points'],p['t'])) is not None]
            if len(distances)>=2:matches.append({'reference_id':ref['id'],'overlap_points':len(distances),
                                                  'rms_m':math.sqrt(sum(v*v for v in distances)/len(distances))})
        scored.append({'candidate_key':tr['source_candidate_key'],'nearest_offline_path':min(matches,key=lambda row:row['rms_m']) if matches else None})
    for ax,title in zip(axes,['Saved offline hypotheses (NOT truth)','Random selected observations + model']):
        ax.set_title(title);ax.scatter([0],[0],marker='s',c='gold',label='platform');ax.set_aspect('equal',adjustable='box');ax.grid(alpha=.25);ax.set_xlabel('East (m)')
        ax.legend(fontsize=7)
    axes[0].set_ylabel('North (m)');fig.tight_layout();fig.savefig(output,dpi=140);plt.close(fig)
    reference_ids={tr['id'] for tr in reference['tracks']}
    represented={row['nearest_offline_path']['reference_id'] for row in scored if row['nearest_offline_path']}
    return {'comparable':True,'reference_is_truth':False,'same_time_nearest_branch_diagnostic':scored,
            'reference_primary_hypotheses':len(reference_ids),'nearest_reference_hypotheses':len(represented),
            'complete_reference_coverage':represented==reference_ids,
            'plot':str(output.resolve()),'no_spatial_return':not bool(spatial['tracks'])}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('result_id');p.add_argument('--seed',type=int,default=20261125)
    p.add_argument('--label',default='baseline');p.add_argument('--run-model',action='store_true')
    p.add_argument('--times',type=int,choices=range(4,17),default=12)
    p.add_argument('--resume-session',help='本地复核该测试已返回的关联，只新增一次绘图调用，不重试关联')
    p.add_argument('--association-only',action='store_true',help='只实测关联，本次绝不调用绘图模型')
    args=p.parse_args()
    batch=json.loads(api.get_gaussian_batch(args.result_id).body)
    ids=select_rows(batch,args.seed,args.times)
    state=create_mixed(batch,max_calls=2)
    directory=api.mixed_path(args.resume_session).parent if args.resume_session else api.ResultRepository(api.MIXED_ROOT).create_run()
    path=directory/'state.json'
    report={'source_result_id':args.result_id,'seed':args.seed,'label':args.label,'session_id':directory.name,
            'selected_ids':ids,'selected_count':len(ids),'real_model_calls':0,'automatic_retry':False}
    if args.resume_session:
        path=api.mixed_path(args.resume_session);directory=path.parent
        report=json.loads((directory/'random_replay_report.json').read_text(encoding='utf-8'))
        if report['source_result_id']!=args.result_id or report['real_model_calls']!=1:raise ValueError('仅支持本工具已完成一次关联的同源测试')
        state=json.loads(path.read_text(encoding='utf-8'))
        if state['status']!='invalid_response':raise ValueError('仅本地恢复已返回但未通过的关联')
        ids=report['selected_ids']
    def save_report():
        (directory/'random_replay_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        api.save_state(path,state)
    def call(context,prompt,phase):
        # All purely local failures must happen before counting a paid attempt.
        from backend.app.infrastructure.llm.client import build_request,endpoint
        settings=api.model_settings()
        endpoint(settings.base_url,settings.api_format)
        build_request([],prompt,settings.model,allow_text_only=True,api_format=settings.api_format)
        if not settings.api_key:raise ValueError('模型凭据未配置；未调用模型')
        state['calls']+=1;report['real_model_calls']+=1;state['status']=phase+'_pending';save_report()
        attempt=directory/f'{phase}_attempt.json'
        api.save_state(attempt,{'status':'pending','input':context,'prompt':prompt})
        from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
        try:response=ConfiguredModelGateway(timeout_override=240).invoke([],prompt,settings,directory/'model_calls','data')
        except Exception as exc:
            report['error_type']=type(exc).__name__
            api.save_state(attempt,{'status':'call_unknown','input':context,'prompt':prompt,
                                   'error_type':type(exc).__name__,'automatic_retry':False})
            state['status']=phase+'_call_unknown';report['error']=phase+' call unknown; no retry';save_report()
            print(json.dumps(report,ensure_ascii=False),flush=True);raise SystemExit(2)
        api.save_state(attempt,{'status':'returned','input':context,'prompt':prompt,'answer':response.get('answer',''),'usage':response.get('usage')})
        return response
    if args.resume_session:
        saved=json.loads((directory/'association_attempt.json').read_text(encoding='utf-8'))
        if saved['status']!='returned':raise ValueError('没有已返回的关联，未知请求禁止重试')
        context=saved['input'];prompt=saved['prompt'];response=saved
        report['local_recovery']=True;report.pop('validation_error',None)
    else:
        context,prompt=prepare_mixed(state,ids,'混合散点随机抽样测试。仅依据提供的观测判断接续，保留未归属和竞争，不预设目标数量。')
    save_report()
    if not args.run_model:
        print(json.dumps({'session_id':directory.name,'selected_count':len(ids),'model_calls':0}));return
    if not args.resume_session:response=call(context,prompt,'association')
    state['status']='ready'
    try:validated=validate_mixed(state,context,response['answer'])
    except (ValueError,TypeError,KeyError) as exc:
        state['status']='invalid_response';report['validation_error']=str(exc);save_report();print(json.dumps(report,ensure_ascii=False));return
    state=update_mixed(state,context,validated,response.get('usage'))
    report['association']=association_score(state,batch);save_report()
    # Isolated evaluation adoption, not adoption into any existing user session.
    state=review_mixed(state,state['version'],True,'独立随机测试集合，非用户生产会话')
    context,prompt=prepare_model_plot(state)
    report['geometry_reference_branches']=len(context['spatial_references']['candidates']);save_report()
    if args.association_only:
        report.update(plot_status='not_requested',target_hypotheses=len(state.get('targets',{})),
                      supported_target_hypotheses=sum(t['evidence']['eligible_for_spatial'] for t in state.get('targets',{}).values()))
        save_report();print(json.dumps(report,ensure_ascii=False),flush=True);return
    if state.get('joint_identity_version')==1 and not context['spatial_references']['candidates']:
        report.update(plot_status='withheld_joint_identity',spatial_branches=0,
                      spatial_reason='没有通过联合身份／多时刻留出检查的配对；本测试不再为已知无空间依据的集合付费绘图。')
        reference=api.SOURCE_OUTPUTS/args.result_id/'tracks.json'
        if reference.is_file():report['comparison']=compare_space({'tracks':[]},reference,directory/'random_comparison.png')
        save_report();print(json.dumps(report,ensure_ascii=False),flush=True);return
    response=call(context,prompt,'plot');state['status']='ready'
    try:state=apply_model_plot(state,context,response['answer'],response.get('usage'))
    except (ValueError,TypeError,KeyError) as exc:
        state['status']='plot_invalid_response';report['validation_error']=str(exc);save_report();print(json.dumps(report,ensure_ascii=False));return
    api.commit_model_plot(path,state)
    spatial=state['rounds'][-1]['model_plot']['spatial_result']
    report.update(plot_status=state['plot_status'],spatial_status=spatial['status'],spatial_branches=len(spatial['tracks']),
                  spatial_reason=spatial['reason'],spatial_errors=spatial.get('validation_errors',[]))
    reference=api.SOURCE_OUTPUTS/args.result_id/'tracks.json'
    if reference.is_file():report['comparison']=compare_space(spatial,reference,directory/'random_comparison.png')
    save_report();print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
