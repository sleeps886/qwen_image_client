"""Real node lifecycle instrumentation. Runtime callback is never checkpoint state."""
from time import perf_counter_ns
from datetime import datetime,timezone

LABELS={
    'knowledge_retrieval':'知识规则检索','pixel_candidates':'绘图区与候选点提取',
    'tracklet_linking':'单阵亮带连续性分析','count_hypothesis':'数量线索判定',
    'association_context':'关联上下文组装','model_association':'模型目标清单与双阵关联',
    'pair_validation':'候选归属与同步校验','count_audit':'数量与覆盖一致性复核',
    'identity_reconciliation':'跨时段身份与串线复核',
    'spatial_localization':'双阵空间定位','continuous_trajectory_estimation':'连续轨迹估计',
    'reprojection_quality_check':'重投影质检','result_summary':'目标数量与定位汇总','render':'空间候选绘图'}

KINDS={'model_association':'model_gateway','knowledge_retrieval':'builtin_rules',
       'pixel_candidates':'local_heuristic','tracklet_linking':'local_heuristic',
       'count_hypothesis':'local_heuristic','identity_reconciliation':'local_heuristic',
       'spatial_localization':'numeric_geometry','continuous_trajectory_estimation':'numeric_optimizer',
       'reprojection_quality_check':'measurement_consistency','render':'local_renderer'}


def start_record(name):
    token=(perf_counter_ns(),datetime.now(timezone.utc).isoformat(timespec='microseconds'))
    return token,{'event':'node','node':name,'label':LABELS.get(name,name),'status':'running',
                  'execution_kind':KINDS.get(name,'local_validation_or_report'),
                  'started_at':token[1],'execution_only_not_accuracy':True}


def end_record(name,token,status,detail):
    return {'event':'node','node':name,'label':LABELS.get(name,name),'status':status,
            'execution_kind':KINDS.get(name,'local_validation_or_report'),
            'started_at':token[1],'finished_at':datetime.now(timezone.utc).isoformat(timespec='microseconds'),
            'elapsed_ms':round((perf_counter_ns()-token[0])/1e6,3),
            'execution_only_not_accuracy':True,'summary':detail}


def summary(state):
    if state.get('failure'):return str(state['failure'])[:200]
    if state.get('count_report'):
        c=state['count_report'];localized=c.get('localized_target_count')
        if c.get('continuous_identity_count') is not None:
            return f"模型原始ID {c['associated_target_count']}，连续身份假设{c['continuous_identity_count']}，可定位身份{localized if localized is not None else '待求解'}；不是已确认目标数"
        return f"已关联{c['associated_target_count']}个目标ID，可定位{localized if localized is not None else '待求解'}；数量状态需独立核验"
    if state.get('document') is not None:
        tracks=state['document'].get('tracks',[])
        return f"空间分支{len(tracks)}段（镜像不算新增目标），观测点{sum(len(t['points']) for t in tracks)}个"
    if state.get('observations') is not None:
        tracks=state['observations'].get('tracks',[])
        rejected=(state['observations'].get('candidate_validation') or {}).get('rejected_pair_count',0)
        return f"已关联{len(set(t['id'] for t in tracks))}个目标ID，{sum(len(t['points']) for t in tracks)}组同步观测"+(f'；隔离{rejected}组非法引用，仅部分观测' if rejected else '')
    if state.get('count_hypothesis'):
        c=state['count_hypothesis'];return f"亮带数量线索约{c['min']}～{c['max']}；不是已确认目标数"
    if state.get('evidence'):
        return f"{len(state['evidence']['images'])}张图，{sum(len(i['candidates']) for i in state['evidence']['images'])}个像素候选"
    if state.get('rules'):return f"检索{len(state['rules'])}条内置规则"
    return '完成'


def observed(name,node,emit=None):
    def call(state):
        token,start=start_record(name)
        if emit:emit(start)
        try:
            update=node(state)
        except Exception as exc:
            if emit:emit(end_record(name,token,'failed',type(exc).__name__ if name=='model_association' else str(exc)[:200]))
            raise
        status='completed'
        if update.get('failure'):status='failed'
        elif state.get('failure'):status='skipped'
        detail=summary({**state,**update})
        if name=='tracklet_linking' and 'tracklets' in update:
            detail=f"连接出{len(update['tracklets'])}条持续亮带片段；双侧走向复核{len(update.get('crossing_report',{}).get('events',[]))}处X交叉延续假设；两阵不相加算目标"
        elif name=='association_context' and 'prompt' in update:
            detail=f"组装{len(state.get('tracklets',[]))}条亮带片段与数量线索，模型输入{len(update['prompt'])}字符"
        elif name=='identity_reconciliation' and 'identity_report' in update:
            r=update['identity_report']
            detail=f"{r['continuous_identity_count']}个连续身份假设，合并{r['merged_model_id_boundaries']}处跨ID断裂，隔离{r['rejected_pair_count']}组不确定配对；不是已确认目标数"
        if emit:emit(end_record(name,token,status,detail))
        return update
    return call
