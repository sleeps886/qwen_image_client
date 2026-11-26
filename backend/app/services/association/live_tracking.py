"""Snapshot preparation and strict v2 validation, independent of HTTP/jobs."""
import copy
import math
from collections import Counter
from backend.app.core.exceptions import InputError
from backend.app.repositories.live_observation_repository import uid, now, LiveConflict
from backend.app.services.association.incremental_evidence import evidence, observability, replay_plan, digest
from backend.app.services.inputs.live_observation_input import blind_point
from backend.app.services.inputs.model_json import association_object

REASONS = {'continuity', 'competing', 'insufficient', 'tentative', 'cross_array', 'turn', 'gap', 'quality'}


def prepare_live(repo, sid, request, model_reference=None):
    state = repo.state(sid)
    cutoff = state['receipt_seq'] if request['knowledge_cutoff_seq'] is None else request['knowledge_cutoff_seq']
    if cutoff > state['receipt_seq']:
        raise InputError('知识截止不能超出当前已接收序号')
    points = [p for p in repo.observations(sid) if p['receipt_seq'] <= cutoff]
    kind = 'analysis' if request['purpose'] == 'association' else 'prediction'
    origin = request['forecast_origin_time']
    if kind == 'prediction' and origin is None:
        raise InputError('预测/重建必须指定事件时间起点')
    if request['purpose'] == 'causal_forecast':
        points = [p for p in points if p['event_time'] <= origin]
    available = {p['observation_id'] for p in points}
    if request['observation_ids'] is not None:
        requested = set(request['observation_ids'])
        if not requested or len(requested) != len(request['observation_ids']) or not requested <= available:
            raise InputError('所选编号为空、重复、未知或超出截止范围；不会静默删除')
        # History is explicit, part of the consented snapshot, never hidden context.
        history = set(state['projection'].get('scope_ids', [])) & available if kind == 'analysis' else set()
        points = [p for p in points if p['observation_id'] in requested | history]
    points.sort(key=lambda p: (p['event_time'], p['platform_id'], p['array'], p['bearing_deg'], p['observation_id']))
    if not points:
        raise InputError('截止范围内尚无有效观测，可先追加单点或批次')
    ids = [p['observation_id'] for p in points]
    config, evidence_cfg = state['configuration'], state['configuration_id']
    if request['purpose'] == 'causal_forecast':
        known = [v for v in repo.items(sid, 'configuration') if v['known_seq'] <= cutoff]
        chosen = max(known, key=lambda v: v['known_seq'])
        config, evidence_cfg = chosen['configuration'], chosen['configuration_id']
    graph = evidence(points, config)
    capability = observability(points, config)
    index = {oid: i+1 for i, oid in enumerate(ids)}
    previous = copy.deepcopy(state['projection'])
    # No future-derived identity, summaries or candidate parameters enter a causal
    # historical forecast. Projecting future-adopted tracks backwards would leak.
    previous_safe = set(previous.get('scope_ids', [])) <= set(ids) and (request['purpose'] != 'causal_forecast' or previous.get('knowledge_seq', 0) <= cutoff)
    current_projection_excluded = not previous_safe
    historical_projection_used = False
    if not previous_safe and request['purpose'] == 'causal_forecast':
        known_projections = [v['projection'] for v in repo.items(sid,'projection')
                             if v['projection'].get('knowledge_seq',0) <= cutoff and set(v['projection'].get('scope_ids',[])) <= set(ids)]
        if known_projections:
            previous = max(known_projections,key=lambda v:v.get('knowledge_seq',0))
            previous_safe = True
            historical_projection_used = True
    existing = previous.get('stream_groups', []) if previous_safe else []
    history = [{k: g[k] for k in ('group_ref', 'platform_id', 'array', 'stable_id') if k in g} |
               {'point_indices': [index[x] for x in g['observation_ids'] if x in index]} for g in existing]
    roles = {'unprocessed': list(range(1,len(ids)+1)), 'adopted': [], 'waiting_evidence': [], 'ambiguous': []}
    if previous_safe:
        for group in existing:
            roles['adopted'].extend(index[x] for x in group['observation_ids'] if x in index)
        for group in previous.get('unresolved_groups', []):
            role = 'ambiguous' if group.get('possible_group_refs') else 'waiting_evidence'
            roles[role].extend(index[x] for x in group['observation_ids'] if x in index)
        known = set(roles['adopted']+roles['waiting_evidence']+roles['ambiguous'])
        roles['unprocessed'] = [i for i in roles['unprocessed'] if i not in known]
    safe_graph = copy.deepcopy(graph)
    for f in safe_graph['fragments']:
        f['point_indices'] = [index[x] for x in f.pop('observation_ids')]
    for conflict in safe_graph['conflict_sets']:
        conflict['point_indices'] = [index[x] for x in conflict.pop('observation_ids')]
    for fragment in safe_graph['cross_array']['fragments']:
        fragment['point_indices'] = [index[x] for x in fragment.pop('observation_ids')]
    columns = ['event_time', 'platform_id', 'array', 'bearing_deg', 'strength', 'quality', 'mixed', 'sigma_deg', 'bearing_error_assumption_deg']
    context = {'format': 'live-input-table-v1', 'purpose': request['purpose'],
               'knowledge_cutoff_seq': cutoff, 'forecast_origin_time': origin, 'horizon': request['horizon'],
               'columns': ['point_index', *columns],
               'rows': [[i+1, *[p.get(k) for k in columns]] for i, p in enumerate(points)],
               'existing_stream_groups': history, 'point_roles': roles,
               'existing_target_hypotheses': [{k:t[k] for k in ('target_ref','stable_id','stream_group_refs','status') if k in t} for t in previous.get('target_hypotheses',[])] if previous_safe else [],
               'evidence': safe_graph, 'observability': capability,
               'configuration': config, 'configuration_id': evidence_cfg, 'base_state_revision': state['state_revision'],
               'event_range': [points[0]['event_time'], points[-1]['event_time']],
               'prompt_template_version': 'live-v2-20261125',
               'reason_codes': sorted(REASONS), 'future_projection_excluded': current_projection_excluded,
               'historical_projection_used': historical_projection_used}
    if model_reference is not None:
        context['model_reference'] = model_reference
    snapshot = {'snapshot_id': uid('snapshot'), 'kind': kind, 'created_at': now(),
                'purpose': request['purpose'], 'scope_ids': ids, 'points': [blind_point(p) for p in points],
                'base_state_revision': state['state_revision'], 'configuration_id': state['configuration_id'],
                'evidence_configuration_id': evidence_cfg,
                'knowledge_cutoff_seq': cutoff, 'forecast_origin_time': origin,
                'event_range': [points[0]['event_time'], points[-1]['event_time']],
                'observability': capability, 'evidence': graph, 'context': context,
                'history_scope_included': len(set(ids) & set(previous.get('scope_ids', []))),
                'replay_plan': replay_plan(points, previous, state['predictions']),
                'parent_prediction_id': next((p['prediction_id'] for p in reversed(state['predictions'])
                    if p['forecast_origin_time'] == origin and p['knowledge_cutoff_seq'] <= cutoff
                    and (p['purpose'] == request['purpose'] or request['purpose'] == 'retrospective_reconstruction')),None)}
    context['snapshot_id'] = snapshot['snapshot_id']
    snapshot['context_digest'] = digest(context)
    snapshot['model_calls'] = 0
    snapshot['input_chars'] = len(__import__('json').dumps(context, ensure_ascii=False))
    snapshot['resource_note'] = '未裁剪观测、未自动拆收费任务；候选窗口预算在evidence中可见。'
    if kind == 'prediction' and capability['level'] == 'observations_only':
        snapshot['prediction_allowed'] = False
        snapshot['prediction_block_reason'] = '尚无至少3个独立时刻的单阵趋势证据；可继续接收，不补造预测'
    elif kind == 'prediction':
        snapshot['prediction_allowed'] = True
    return repo.freeze(sid, state, snapshot)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value)-set(required)-set(optional) or not set(required) <= set(value):
        raise ValueError('回复字段缺失或包含未约定字段')


def _indices(indices, size):
    if not isinstance(indices, list) or not indices or any(type(i) is not int or not 1 <= i <= size for i in indices) or len(set(indices)) != len(indices):
        raise ValueError('观测行号无效、越界或重复；未猜测修复编号')
    return indices


def validate_association(answer, snapshot):
    value, _ = association_object(answer)
    _keys(value, ('format', 'snapshot_id', 'base_state_revision', 'stream_groups', 'target_hypotheses', 'unresolved_groups'))
    if value['format'] != 'association-compact-v2' or value['snapshot_id'] != snapshot['snapshot_id'] or type(value['base_state_revision']) is not int or value['base_state_revision'] != snapshot['base_state_revision']:
        raise ValueError('协议、快照或投影版本不匹配')
    if any(not isinstance(value[k], list) for k in ('stream_groups', 'target_hypotheses', 'unresolved_groups')):
        raise ValueError('链、目标和待证据字段必须是数组')
    points = snapshot['points']; ids = snapshot['scope_ids']; size = len(ids)
    fragments = {r['reference_id']: r for r in snapshot['evidence']['fragments']}
    occupied, groups, refs = set(), [], set()
    for row in value['stream_groups']:
        _keys(row, ('group_ref', 'reason_code'), ('point_indices', 'fragment_refs'))
        if not isinstance(row['group_ref'], str) or not row['group_ref'] or row['group_ref'] in refs or row['reason_code'] not in REASONS:
            raise ValueError('片段链引用或理由无效')
        refs.add(row['group_ref'])
        support = set()
        if 'point_indices' in row:
            support.update(ids[i-1] for i in _indices(row['point_indices'], size))
        if 'fragment_refs' in row:
            if not isinstance(row['fragment_refs'], list) or not row['fragment_refs'] or len(set(row['fragment_refs'])) != len(row['fragment_refs']):
                raise ValueError('候选引用须非空且不重复')
            for ref in row['fragment_refs']:
                if ref not in fragments:
                    raise ValueError('候选引用不存在于冻结快照')
                support.update(fragments[ref]['observation_ids'])
        if not support or occupied & support:
            raise ValueError('片段链为空或确定观测重复占用')
        selected = [p for p in points if p['observation_id'] in support]
        streams = {(p['platform_id'], p['array']) for p in selected}
        if len(streams) != 1 or len({p['event_time'] for p in selected}) != len(selected):
            raise ValueError('同阵链混入不同平台/阵列或同时间竞争点')
        selected.sort(key=lambda p: p['event_time'])
        algorithm = snapshot['evidence']['parameters']
        continuity = []
        for a,b in zip(selected, selected[1:]):
            dt = b['event_time']-a['event_time']
            delta = abs(b['bearing_deg']-a['bearing_deg'])
            gate = algorithm['continuation_gate_deg']+dt*algorithm['max_rate_deg_per_second']
            if delta > gate:
                raise ValueError('同阵链接续超过当前配置门限；可保留分裂片段或待决，不能强制合并')
            continuity.append({'from':a['observation_id'],'to':b['observation_id'],'dt':dt,'delta_deg':delta,'gate_deg':gate})
        occupied.update(support)
        key = next(iter(streams))
        groups.append({'group_ref': row['group_ref'], 'observation_ids': sorted(support),
                       'platform_id': key[0], 'array': key[1], 'reason_code': row['reason_code'],
                       'support_times': len(selected), 'status': 'tentative'})
        groups[-1]['continuity_checks'] = continuity
        groups[-1]['evidence_level'] = 'temporal_fragment_candidate' if len(selected)>=3 else 'waiting_temporal_support'
        groups[-1]['gate_calibrated'] = False
    targets, target_refs, used_groups = [], set(), set()
    for row in value['target_hypotheses']:
        _keys(row, ('target_ref', 'stream_group_refs', 'reason_code'))
        members = row['stream_group_refs']
        if not isinstance(row['target_ref'], str) or not row['target_ref'] or row['target_ref'] in target_refs or row['reason_code'] not in REASONS:
            raise ValueError('目标引用或理由无效')
        if not isinstance(members, list) or not members or len(set(members)) != len(members) or not set(members) <= refs or used_groups & set(members):
            raise ValueError('目标引用不存在、重复或片段链重复占用')
        chains = [g for g in groups if g['group_ref'] in members]
        if len({g['platform_id'] for g in chains}) != 1 or len({g['array'] for g in chains}) != len(chains):
            raise ValueError('同目标的同阵多片段须先组成同阵链；不支持未经验证跨平台融合')
        target_refs.add(row['target_ref']); used_groups.update(members)
        targets.append({**row, 'identity_confirmed': False, 'spatial_qualified': False, 'status': 'tentative'})
        if len(chains) == 2:
            from backend.app.services.association.joint_identity import pair_evidence
            lookup = {p['observation_id']: {**p, 't': p['event_time']} for p in points}
            proof = pair_evidence([lookup[x] for x in chains[0]['observation_ids']],
                                  [lookup[x] for x in chains[1]['observation_ids']],
                                  snapshot['context']['configuration'].get('platform_config'))
            targets[-1]['cross_array_evidence'] = proof
            targets[-1]['spatial_candidate_supported'] = proof['eligible_for_spatial']
            # Passing a synchronous numerical check is not authorization for a
            # spatial output. The live predictor remains explicitly bearing-only.
    unresolved, unresolved_ids = [], set()
    for row in value['unresolved_groups']:
        _keys(row, ('point_indices', 'reason_code'), ('possible_group_refs',))
        if row['reason_code'] not in REASONS:
            raise ValueError('未归属原因无效')
        support = {ids[i-1] for i in _indices(row['point_indices'], size)}
        if occupied & support or unresolved_ids & support:
            raise ValueError('未归属/歧义与确定占用重叠')
        possibilities = row.get('possible_group_refs', [])
        if not isinstance(possibilities, list) or len(set(possibilities)) != len(possibilities) or not set(possibilities) <= refs:
            raise ValueError('待决片段链引用无效')
        unresolved_ids.update(support)
        unresolved.append({'observation_ids': sorted(support), 'reason_code': row['reason_code'],
                           'possible_group_refs': possibilities, 'status': 'ambiguous' if possibilities else 'waiting_evidence'})
    if occupied | unresolved_ids != set(ids):
        raise ValueError('冻结快照有观测未说明去向，不自动补入或裁掉')
    count = len(occupied)
    return {'stream_groups': groups, 'target_hypotheses': targets, 'unresolved_groups': unresolved,
            'quality': {'assigned': count, 'waiting_evidence': sum(len(r['observation_ids']) for r in unresolved if not r['possible_group_refs']),
                        'ambiguous': sum(len(r['observation_ids']) for r in unresolved if r['possible_group_refs']),
                        'coverage': count/size, 'warnings': ['低覆盖：暂缺证据不等于无效观测'] if count/size < .5 else []}}


def validate_prediction(answer, snapshot):
    value, _ = association_object(answer)
    _keys(value, ('format', 'snapshot_id', 'purpose', 'curves'))
    if value['format'] != 'bearing-prediction-v1' or value['snapshot_id'] != snapshot['snapshot_id'] or value['purpose'] != snapshot['purpose']:
        raise ValueError('预测协议、快照或用途不匹配')
    if not isinstance(value['curves'], list):
        raise ValueError('预测曲线必须是数组')
    ids = snapshot['scope_ids']; points = snapshot['points']; curves = []; used = set()
    origin, horizon = snapshot['forecast_origin_time'], snapshot['context']['horizon']
    for row in value['curves']:
        _keys(row, ('support_indices', 'samples', 'reason_code'))
        support = [points[i-1] for i in _indices(row['support_indices'], len(ids))]
        if len({p['event_time'] for p in support}) < 3 or len({(p['platform_id'], p['array']) for p in support}) != 1:
            raise ValueError('预测曲线必须有至少3个真实独立时刻的单阵支持')
        if len({p['event_time'] for p in support}) != len(support):
            raise ValueError('预测支持存在同阵同时间竞争点')
        ordered = sorted(support,key=lambda p:p['event_time'])
        parameters = snapshot['evidence']['parameters']
        if any(abs(b['bearing_deg']-a['bearing_deg']) > parameters['continuation_gate_deg']+(b['event_time']-a['event_time'])*parameters['max_rate_deg_per_second'] for a,b in zip(ordered,ordered[1:])):
            raise ValueError('预测支持的接续超过配置门限；不跨明显不兼容片段造曲线')
        known_groups = [set(g['point_indices']) for g in snapshot['context']['existing_stream_groups']]
        if sum(bool(set(row['support_indices']) & group) for group in known_groups)>1:
            raise ValueError('预测混用不同已采用同阵集合，请先重新关联并确认')
        support_ids = {p['observation_id'] for p in support}
        if used & support_ids or row['reason_code'] not in REASONS:
            raise ValueError('预测支持重复占用或理由无效')
        used.update(support_ids)
        if not isinstance(row['samples'], list) or not row['samples']:
            raise ValueError('预测曲线缺少样本')
        previous = -math.inf
        for sample in row['samples']:
            _keys(sample, ('event_time', 'bearing_deg', 'uncertainty_deg'))
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in sample.values()):
                raise ValueError('预测样本数值无效')
            t = sample['event_time']
            if t <= previous or not 0 <= sample['bearing_deg'] <= 180 or not 0 <= sample['uncertainty_deg'] <= 180:
                raise ValueError('预测时序、方位或不确定性无效')
            if snapshot['purpose'] == 'causal_forecast' and not origin < t <= origin+horizon:
                raise ValueError('未来预测超出授权区间')
            if snapshot['purpose'] == 'retrospective_reconstruction' and not snapshot['event_range'][0] <= t <= snapshot['event_range'][1]:
                raise ValueError('事后重建超出授权观测范围')
            previous = t
        curves.append({'support_ids': sorted(support_ids), 'platform_id': support[0]['platform_id'], 'array': support[0]['array'],
                       'samples': row['samples'], 'uncertainty_calibrated': False, 'source': 'model_generated_not_observed'})
    return {'curves': curves, 'capability': 'bearing_only', 'spatial_qualified': False,
            'note': '模型生成方位趋势，不是实际观测或确定空间位置；不确定性未标定。'}


ASSOCIATION_PROMPT = '''你是增量方位轨迹关联助手。只用本快照观测、历史与候选证据；来源/文件不代表目标。
候选只是参考，不强制归属，折叠方位多解，少点样本内拟合不能证明身份。单点可待证据。
同阵多个断段可组成一个stream_group；同阵同时间多个候选不能确定归同一链；每点确定占用至多一次。
不同阵链可提出物理目标假设，不代表可空间定位。每个点必须出现在一条链或一个unresolved_groups中。
只输出JSON：{"format":"association-compact-v2","snapshot_id":"输入快照ID","base_state_revision":输入版本,
"stream_groups":[{"group_ref":"G1","fragment_refs":["有效R引用"],"reason_code":"continuity"}],
"target_hypotheses":[{"target_ref":"H1","stream_group_refs":["G1"],"reason_code":"tentative"}],
"unresolved_groups":[{"point_indices":[1],"reason_code":"insufficient"}]}。
stream_groups可用point_indices替代或补充fragment_refs。理由仅用输入reason_codes。歧义项可用possible_group_refs。
不要输出位置、距离、未来预测、长篇解释或隐含省略观测。'''

PREDICTION_PROMPT = '''仅按输入快照真实时刻观测生成方位趋势，不输出距离或空间坐标。
因果预测只用起点及之前已知数据，样本时间必须大于forecast_origin_time且不超过horizon。
事后重建只覆盖输入event_range对应观测范围，并明确purpose，不冒充当时预测。
一条曲线的support_indices只用同平台同阵列至少3个独立时刻，每时刻一个点，不强制合并竞争目标。
证据不足可curves为空。只输出JSON：{"format":"bearing-prediction-v1","snapshot_id":"输入ID",
"purpose":"输入用途","curves":[{"support_indices":[1,2,3],"reason_code":"tentative",
"samples":[{"event_time":未来时间,"bearing_deg":0到180,"uncertainty_deg":0到180}]}]}。
不确定性只是未标定模型估计，不声称高置信概率。'''
