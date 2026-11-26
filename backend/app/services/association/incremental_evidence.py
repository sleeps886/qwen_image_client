"""Deterministic rolling evidence. Candidates are never adopted identities."""
import hashlib
import json
from collections import defaultdict
from backend.app.services.association.tracklet_references import _window_tracklets

DEFAULT_ALGORITHM = {'window_times': 8, 'stride_times': 4, 'candidate_budget_per_window': 6,
                     'max_rate_deg_per_second': 5.0, 'continuation_gate_deg': 15.0}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def evidence(points, configuration):
    algorithm = {**DEFAULT_ALGORITHM, **configuration.get('algorithm', {})}
    streams = defaultdict(list)
    for point in points:
        streams[(point['platform_id'], point['array'])].append(point)
    fragments, windows, conflicts = [], [], []
    seen = set()
    for key, rows in sorted(streams.items()):
        rows.sort(key=lambda p: (p['event_time'], p['bearing_deg'], p['observation_id']))
        times = sorted({p['event_time'] for p in rows})
        by_time = defaultdict(list)
        for row in rows:
            by_time[row['event_time']].append(row)
        for t, group in by_time.items():
            if len(group) > 1:
                conflicts.append({'platform_id': key[0], 'array': key[1], 'event_time': t,
                                  'observation_ids': [p['observation_id'] for p in group],
                                  'reason_code': 'same_stream_same_time_candidates'})
        width, stride = algorithm['window_times'], algorithm['stride_times']
        starts = sorted({*range(0, max(1, len(times)-width+1), stride), max(0, len(times)-width)})
        for start in starts:
            selected = times[start:start+width]
            local = [p for t in selected for p in by_time[t]]
            if not local:
                continue
            window = {'platform_id': key[0], 'array': key[1], 'event_range': [selected[0], selected[-1]],
                      'point_count': len(local), 'independent_times': len(selected),
                      'candidate_budget': algorithm['candidate_budget_per_window'], 'reference_only': True}
            if len(selected) < 4:
                window['not_expanded_reason'] = 'insufficient_for_current_polynomial_candidate_path'
                windows.append(window)
                continue
            compatible = [{**p, 't': p['event_time'], 'candidate_id': p['observation_id']} for p in local]
            candidates = _window_tracklets(compatible, {p['observation_id']: p['observation_id'] for p in local},max_candidates=algorithm['candidate_budget_per_window'])
            window['returned_candidates'] = len(candidates)
            window['budget_limited_search'] = True
            windows.append(window)
            for candidate in candidates[:algorithm['candidate_budget_per_window']]:
                ids = candidate['observation_ids']
                marker = tuple(sorted(ids))
                if marker in seen:
                    continue
                seen.add(marker)
                fragments.append({**candidate, 'reference_id': 'R'+digest(marker)[:16],
                                  'observed_time_window': window['event_range']})
    lookup = {p['observation_id']: p for p in points}
    edges = []
    # Adjacent-window comparisons, retaining multiple compatible branches.
    for left in fragments:
        left_ids = set(left['observation_ids'])
        lpoints = sorted((lookup[x] for x in left_ids), key=lambda p: p['event_time'])
        for right in fragments:
            if left['reference_id'] >= right['reference_id'] or (left['platform_id'], left['array']) != (right['platform_id'], right['array']):
                continue
            right_ids = set(right['observation_ids'])
            rpoints = sorted((lookup[x] for x in right_ids), key=lambda p: p['event_time'])
            overlap = left_ids & right_ids
            competing = any(a['event_time'] == b['event_time'] and a['observation_id'] != b['observation_id'] for a in lpoints for b in rpoints)
            if competing:
                kind = 'conflict'
            elif overlap:
                kind = 'shared_support'
            else:
                a, b = (lpoints[-1], rpoints[0]) if lpoints[-1]['event_time'] < rpoints[0]['event_time'] else (rpoints[-1], lpoints[0])
                dt = b['event_time']-a['event_time']
                if dt <= 0 or abs(b['bearing_deg']-a['bearing_deg']) > algorithm['continuation_gate_deg']+dt*algorithm['max_rate_deg_per_second']:
                    continue
                kind = 'possible_continuation'
            edges.append({'left': left['reference_id'], 'right': right['reference_id'], 'kind': kind, 'reference_only': True})
    covered = {x for f in fragments for x in f['observation_ids']}
    # Reuse the separately validated synchronous path without ever synthesizing
    # matching times. Its numerical shortlist limit is reported, not input loss.
    from backend.app.services.association.joint_identity import prepare_identity_context
    compatible = [{**p, 't': p['event_time']} for p in points]
    joint = prepare_identity_context(compatible, fragments, {}, configuration.get('platform_config'))
    return {'algorithm_version': 'rolling-competition-v1', 'parameters': algorithm, 'candidate_random_seed': 19,
            'parameters_calibrated': False, 'fragments': fragments, 'windows': windows,
            'continuation_edges': edges, 'conflict_sets': conflicts,
            'cross_array': joint,
            'coverage': {'candidate_covered': len(covered), 'total': len(points)},
            'warnings': ['候选搜索有显式窗口预算；未展开分支不等于杂波。', '折叠方位多解；片段、链、物理目标不是同一层级。']}


def observability(points, configuration):
    times = defaultdict(set)
    streams = defaultdict(set)
    for p in points:
        times[(p['platform_id'], p['event_time'])].add(p['array'])
        streams[(p['platform_id'], p['array'])].add(p['event_time'])
    dual = sum(v == {'hull', 'tow'} for v in times.values())
    temporal = any(len(v) >= 3 for v in streams.values())
    return {'level': 'bearing_trend_candidate' if temporal else 'observations_only',
            'strict_synchronous_groups': dual, 'independent_stream_times': [
                {'platform_id': k[0], 'array': k[1], 'times': len(v)} for k, v in sorted(streams.items())],
            'spatial_qualified': False, 'asynchronous_spatial': 'experimental_not_validated',
            'missing': ([] if temporal else ['insufficient_temporal_support']) +
                       ([] if configuration.get('platform_config') else ['platform_geometry']) + ['validated_target_specific_spatial_evidence'],
            'note': '同步计数不代表同目标配对；异步仍可关联方位片段，不伪造同一时刻。'}


def replay_plan(points, previous, predictions):
    previous_ids = set(previous.get('scope_ids', []))
    fresh = [p for p in points if p['observation_id'] not in previous_ids]
    previous_end = previous.get('event_range', [None, None])[-1]
    late = [p for p in fresh if previous_end is not None and p['event_time'] < previous_end]
    if not late:
        return {'required': False, 'affected_observation_ids': [], 'affected_predictions': []}
    # Conservative connected-component expansion: all history of touched streams
    # plus every target-linked stream. Never claim a narrow unproven locality.
    keys = {(p['platform_id'], p['array']) for p in late}
    lookup = {p['observation_id']: p for p in points}
    groups = previous.get('stream_groups', [])
    targets = previous.get('target_hypotheses', [])
    changed = True
    while changed:
        changed = False
        touched = {g['group_ref'] for g in groups if any(x in lookup and (lookup[x]['platform_id'], lookup[x]['array']) in keys for x in g['observation_ids'])}
        linked = {x for t in targets if set(t['stream_group_refs']) & touched for x in t['stream_group_refs']}
        for g in groups:
            if g['group_ref'] in linked:
                for oid in g['observation_ids']:
                    if oid in lookup:
                        key = (lookup[oid]['platform_id'], lookup[oid]['array'])
                        if key not in keys:
                            keys.add(key); changed = True
    affected = [p['observation_id'] for p in points if (p['platform_id'], p['array']) in keys]
    uncertain_dependencies = bool(previous.get('unresolved_groups')) or not previous.get('target_hypotheses')
    if uncertain_dependencies:
        affected = [p['observation_id'] for p in points]
    return {'required': True, 'strategy': 'conservative_stream_dependency_component',
            'expanded_to_full_snapshot': uncertain_dependencies,
            'affected_from': min(p['event_time'] for p in late), 'affected_observation_ids': affected,
            'affected_predictions': [p['prediction_id'] for p in predictions if set(p.get('scope_ids', [])) & set(affected)],
            'old_predictions_immutable': True}

