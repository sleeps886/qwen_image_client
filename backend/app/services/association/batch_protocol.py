"""Lossless, deduplicated batch input and short enumerated output contract."""
import copy
from backend.app.models.association_wire import pack,unpack

FORMAT='batch-compact-v1'
REASONS={
    'continuity':'支持接续，仍为可修订工作假设。','insert':'支持插入历史中段。',
    'turn':'支持同一轨迹转折。','new_target':'建议建立新目标假设。',
    'competing':'存在竞争归属，需保留待定。','insufficient':'证据不足，保留未归属。',
    'cross_array':'存在双阵一致性证据，仍非身份真值。','stable':'观测变化平稳。',
    'tentative':'暂定归属，允许后续修订。','outlier':'当前观测与接续证据不一致。',
}


def reason(row,strict=False):
    if 'reason_code' in row:
        code=row['reason_code']
        if not isinstance(code,str) or code not in REASONS:raise ValueError('reason_code不在允许枚举中')
        if strict and 'reason' in row:raise ValueError('紧凑协议禁止自由文本reason，只返回reason_code')
        return REASONS[code]
    if strict:raise ValueError('紧凑协议必须返回reason_code，禁止自由文本理由')
    text=row.get('reason')
    if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('缺少有效归属理由')
    return text


def encode(context):
    wire=copy.deepcopy(context);observations=[];lookup={}
    def register(p):
        oid=p['observation_id']
        if oid in lookup:
            if lookup[oid]!=p:raise ValueError('同一观测在快照与批次中数值不一致')
        else:lookup[oid]=copy.deepcopy(p);observations.append(copy.deepcopy(p))
        return oid
    snap=wire['snapshot'];snap['observation_ids']=[register(p) for p in snap.pop('observations')]
    for unit in wire['batch_units']:unit['observation_ids']=[register(p) for p in unit.pop('observations')]
    removed={}
    for name in ('observations','current_unit_observations'):
        if name in wire:removed[name]=[register(p) for p in wire.pop(name)]
    if snap.get('platform_configuration')==wire.get('platform_configuration') and 'platform_configuration' in snap:
        snap.pop('platform_configuration');wire['shared_platform_configuration']=True
    if 'position_candidates' in wire:wire['position_candidates_table']=pack(wire.pop('position_candidates'))
    if 'worker_proposals' in wire:
        proposals=wire.pop('worker_proposals')
        # Generated label duplicates the enum; preserve legacy free reasons.
        wire['worker_proposals_table']=pack([{k:v for k,v in p.items() if k!='reason' or 'reason_code' not in p} for p in proposals])
    wire['observations_table']=pack(observations);wire['input_reference_lists']=removed
    wire['wire_format']=FORMAT
    return wire


def decode(wire):
    """Audit helper: exact context roundtrip except generated enum labels."""
    context=copy.deepcopy(wire);context.pop('wire_format')
    observations=unpack(context.pop('observations_table'));lookup={p['observation_id']:p for p in observations}
    def rows(ids):return [copy.deepcopy(lookup[oid]) for oid in ids]
    snap=context['snapshot'];snap['observations']=rows(snap.pop('observation_ids'))
    for unit in context['batch_units']:unit['observations']=rows(unit.pop('observation_ids'))
    for name,ids in context.pop('input_reference_lists').items():context[name]=rows(ids)
    if context.pop('shared_platform_configuration',False):snap['platform_configuration']=copy.deepcopy(context['platform_configuration'])
    if 'position_candidates_table' in context:context['position_candidates']=unpack(context.pop('position_candidates_table'))
    if 'worker_proposals_table' in context:
        context['worker_proposals']=unpack(context.pop('worker_proposals_table'))
        for p in context['worker_proposals']:
            if 'reason_code' in p:p['reason']=reason(p)
    return context


def instruction(kind,base):
    common=(' 输入采用无损表：observations_table中columns对应rows，shared是共有字段，presence是可选字段位掩码；'
        '各组和历史用observation_ids引用该表，不得把编号尾数当行号。position_candidates_table及worker_proposals_table采用相同表格式。'
        '本协议禁止任何自由文本reason，只返回reason_code；允许值为'+','.join(REASONS)+'。'
        '正常待定是有效判断，不要为通过校验强造目标。retry_feedback仅说明上一尝试的结构错误，不是归属答案。')
    # Remove old free-reason output example; keep its association safety rules.
    base=base.split('只返回JSON：',1)[0]
    if kind=='worker':shape='{"snapshot_version":整数,"target_ref":"已有目标ID或new或unassigned","reason_code":"continuity等枚举"}'
    else:shape='{"snapshot_version":整数,"assignments":[{"unit_id":"原样复制本批ID","target_ref":"已有ID或N1等或null","reason_code":"continuity等枚举"}],"revisions":[]}'
    return base+common+' 只返回一次完整JSON，不输出Markdown、长解释或草稿。返回形状：'+shape
