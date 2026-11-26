"""Numerical candidate cards, NOT target assignments or selected mirror truth.

Fit competing constant-velocity pieces to the supplied visible geometry only.
The caller must get a model decision before applying any candidate card.
"""
import math
import random
import numpy as np


def prepare_hypotheses(units, *, residual_gate_m=35., max_speed_mps=100., min_support=4, max_cards=24, trials=2400):
    if len(units) > 1000 or not 0 < residual_gate_m <= 500 or not 0 < max_speed_mps <= 1000:
        raise ValueError('候选证据参数越界')
    if len({u['unit_id'] for u in units}) != len(units):
        raise ValueError('重复输入单元')
    cards = []
    for platform in sorted({u['platform_id'] for u in units}):
        rows = [(u['unit_id'], float(u['t']), c['branch_ref'], float(c['x']), float(c['y']))
            for u in units if u['platform_id'] == platform for c in u.get('candidates', [])]
        rows.sort(key=lambda r: (r[1], r[0], r[2]))
        if len(rows) < min_support:
            continue
        if not all(math.isfinite(v) for r in rows for v in (r[1], r[3], r[4])):
            raise ValueError('非有限候选坐标')
        times = np.array([r[1] for r in rows]); xy = np.array([[r[3], r[4]] for r in rows])
        proposals = {}; rng = random.Random(2026)
        def support(origin, velocity, t0, gate):
            residual = np.linalg.norm(xy-(origin+(times-t0)[:, None]*velocity), axis=1)
            # At most one measured candidate at one platform/time per piece.
            best = {}
            for index in np.flatnonzero(residual <= gate):
                stamp = rows[index][1]
                if stamp not in best or residual[index] < residual[best[stamp]]:
                    best[stamp] = int(index)
            return tuple(sorted(best.values())), residual
        for trial in range(trials):
            gate = residual_gate_m if trial % 2 else residual_gate_m/3
            i, j = rng.sample(range(len(rows)), 2)
            dt = times[j]-times[i]
            if abs(dt) < 10 or rows[i][0] == rows[j][0]:
                continue
            velocity = (xy[j]-xy[i])/dt
            if np.linalg.norm(velocity) > max_speed_mps:
                continue
            indices, residual = support(xy[i], velocity, times[i], gate)
            if len(indices) < min_support:
                continue
            t0 = min(times[list(indices)]); span = max(times[list(indices)])-t0
            if span < 15:
                continue
            # Refit the same observed support, never manufacture coordinates.
            design = np.column_stack((np.ones(len(indices)), times[list(indices)]-t0))
            fit = np.linalg.lstsq(design, xy[list(indices)], rcond=None)[0]
            if np.linalg.norm(fit[1]) > max_speed_mps:
                continue
            indices, residual = support(fit[0], fit[1], t0, gate)
            if len(indices) < min_support:
                continue
            score = len(indices)-float(np.mean(residual[list(indices)]))/residual_gate_m
            key = tuple((rows[k][0], rows[k][2]) for k in indices)
            if key not in proposals or score > proposals[key]['score']:
                proposals[key] = {'indices': indices, 'fit_origin': fit[0].tolist(), 'fit_velocity': fit[1].tolist(),
                    'fit_t0': float(t0), 'rmse_m': float(np.sqrt(np.mean(residual[list(indices)]**2))), 'score': score}
        selected = []
        for key, proposal in sorted(proposals.items(), key=lambda item: (-item[1]['score'], item[0])):
            members = set(key)
            if any(len(members & old)/min(len(members), len(old)) > .7 and proposal['rmse_m'] >= old_rmse*.5
                for old, old_rmse in selected):
                continue
            selected.append((members, proposal['rmse_m']))
            indices = proposal['indices']
            cards.append({'hypothesis_ref': f'L{len(cards)+1:03d}', 'platform_id': platform,
                'members': [{'unit_id': rows[k][0], 'branch_ref': rows[k][2]} for k in indices],
                'time_range': [float(min(times[list(indices)])), float(max(times[list(indices)]))],
                'fit_velocity_mps': proposal['fit_velocity'], 'fit_origin_xy': proposal['fit_origin'], 'fit_t0': proposal['fit_t0'],
                'fit_rmse_m': proposal['rmse_m'], 'support_count': len(indices), 'identity_confirmed': False,
                'validation': 'in_sample_candidate_not_independent_identity_evidence'})
            if len(selected) >= max_cards:
                break
    # These are possible joins, not mandatory decisions or accepted graph edges.
    lookup = {u['unit_id']: u for u in units}; joins = []
    def measured(member):
        unit = lookup[member['unit_id']]
        p = next(c for c in unit['candidates'] if c['branch_ref'] == member['branch_ref'])
        return unit['t'], p['x'], p['y']
    for a in cards:
        for b in cards:
            if a is b or a['platform_id'] != b['platform_id']:
                continue
            am = {m['unit_id']: m['branch_ref'] for m in a['members']}
            bm = {m['unit_id']: m['branch_ref'] for m in b['members']}
            common = set(am) & set(bm)
            shared_boundary = bool(common and a['time_range'][1] == b['time_range'][0]
                and all(am[uid] == bm[uid] for uid in common))
            if a['time_range'][1] > b['time_range'][0] or a['time_range'][1] == b['time_range'][0] and not shared_boundary or common and not shared_boundary:
                continue
            ta, ax, ay = measured(a['members'][-1]); tb, bx, by = measured(b['members'][0])
            speed = math.hypot(bx-ax, by-ay)/(tb-ta) if tb > ta else 0.
            if speed <= max_speed_mps:
                va = a['fit_velocity_mps']; vb = b['fit_velocity_mps']
                ac = np.array(a['fit_origin_xy'])-np.array(va)*a['fit_t0']
                bc = np.array(b['fit_origin_xy'])-np.array(vb)*b['fit_t0']
                dv = np.array(va)-np.array(vb); dc = ac-bc
                turn = -float(np.dot(dc, dv))/float(np.dot(dv, dv)) if float(np.dot(dv, dv)) > 1e-10 else (ta+tb)/2
                bounded_turn = max(ta, min(tb, turn))
                turn_residual = float(np.linalg.norm(dc+dv*bounded_turn))
                speed_ratio = max(math.hypot(*va), .01)/max(math.hypot(*vb), .01)
                joins.append({'from': a['hypothesis_ref'], 'to': b['hypothesis_ref'], 'gap_seconds': tb-ta,
                    'shared_measured_boundary': shared_boundary,
                    'endpoint_speed_mps': speed, 'velocity_change_mps': math.dist(va, vb),
                    'constant_velocity_residual_m': math.hypot(bx-ax-va[0]*(tb-ta), by-ay-va[1]*(tb-ta)),
                    'single_turn_time': bounded_turn, 'single_turn_residual_m': turn_residual,
                    'speed_ratio': speed_ratio,
                    'candidate_join_cost': turn_residual/residual_gate_m+2*abs(math.log(speed_ratio))})
    return {'protocol': 'spatial-candidate-cards-v1', 'hypotheses': cards, 'possible_joins': joins,
        'uncovered_units': [u['unit_id'] for u in units if not any(u['unit_id'] == m['unit_id'] for c in cards for m in c['members'])],
        'parameters': {'residual_gate_m': residual_gate_m, 'max_speed_mps': max_speed_mps, 'min_support': min_support},
        'notice': '数值候选，不是已确认目标；模型可拒绝、拆分、择支或保留竞争；没有固定目标数量。'}


def partition_proposals(evidence, units, history_assignments=(), *, beam_width=256, max_plans=8):
    """Competing set-pack/one-turn proposals; no candidate is committed locally.

Score assumptions are explicit, not fitted against evaluation/source identity.
Keep whole mirror alternatives and let the model select or reject a proposal.
"""
    cards = evidence['hypotheses']; lookup = {c['hypothesis_ref']: c for c in cards}
    join_lookup = {(j['from'], j['to']): j for j in evidence['possible_joins']}
    beam = [(0., (), frozenset())]
    for card in cards:
        members = frozenset((m['unit_id'], m['branch_ref']) for m in card['members'])
        expanded = list(beam)
        for score, chosen, occupied in beam:
            old_branches = dict(occupied)
            if any(uid in old_branches and old_branches[uid] != branch for uid, branch in members):
                continue
            compatible = True
            for ref in chosen:
                old_members = {(m['unit_id'], m['branch_ref']) for m in lookup[ref]['members']}
                if members.intersection(old_members) and not ((ref, card['hypothesis_ref']) in join_lookup or (card['hypothesis_ref'], ref) in join_lookup):
                    compatible = False; break
            added = len(members-occupied)
            if compatible and added:
                benefit = added*3.-2.-card['fit_rmse_m']/evidence['parameters']['residual_gate_m']
                expanded.append((score+benefit, (*chosen, card['hypothesis_ref']), occupied|members))
        expanded.sort(key=lambda x: (-x[0], x[1]))
        beam = expanded[:beam_width]
    joins = [j for j in evidence['possible_joins'] if j['candidate_join_cost'] < 2.5]
    joins.sort(key=lambda j: (j['candidate_join_cost'], j['from'], j['to']))
    proposals = []; old = {r['unit_id']: r for r in history_assignments}
    for score, chosen, occupied in beam:
        # A min-cost matching of piece endpoints is only a numerical proposal.
        required = [j for j in evidence['possible_joins'] if j['from'] in chosen and j['to'] in chosen and j['shared_measured_boundary']]
        if len({j['from'] for j in required}) != len(required) or len({j['to'] for j in required}) != len(required):
            continue
        states = [(sum(2.5-j['candidate_join_cost'] for j in required), tuple(required),
            frozenset(j['from'] for j in required), frozenset(j['to'] for j in required))]
        for join in joins:
            if join['from'] not in chosen or join['to'] not in chosen or join in required:
                continue
            expanded = list(states)
            for join_score, selected, outgoing, incoming in states:
                if join['from'] not in outgoing and join['to'] not in incoming:
                    expanded.append((join_score+2.5-join['candidate_join_cost'], (*selected, join), outgoing|{join['from']}, incoming|{join['to']}))
            expanded.sort(key=lambda x: -x[0]); states = expanded[:32]
        for join_score, selected, outgoing, incoming in states[:2]:
            successors = {j['from']: j['to'] for j in selected}
            chains = []
            for ref in chosen:
                if ref in incoming:
                    continue
                path = [ref]
                while path[-1] in successors:
                    path.append(successors[path[-1]])
                chains.append(path)
            chains.sort(key=lambda c: (lookup[c[0]]['time_range'][0], c))
            branch_support = 0; chain_rows = []
            for i, chain in enumerate(chains):
                hist_targets = {}
                for ref in chain:
                    for member in lookup[ref]['members']:
                        previous = old.get(member['unit_id'])
                        if previous:
                            branch_support += previous.get('branch_ref') == member['branch_ref']
                            target = previous.get('target_ref')
                            if target:
                                hist_targets[target] = hist_targets.get(target, 0)+1
                chain_rows.append({'chain_ref': f'C{i+1:02d}', 'hypothesis_refs': chain,
                    'provisional_history_target_support': hist_targets})
            proposals.append({'numerical_score': score+join_score, 'coverage': len(occupied),
                'unassigned_units': [u['unit_id'] for u in units if u['unit_id'] not in dict(occupied)],
                'chains': chain_rows, 'joins': list(selected), 'history_branch_support_count': branch_support})
    proposals.sort(key=lambda p: (-round(p['numerical_score'], 3), -p['history_branch_support_count'], json_key(p['chains'])))
    selected = []; fingerprints = set(); topology_counts = {}
    for plan in proposals:
        key = json_key([c['hypothesis_refs'] for c in plan['chains']])
        if key in fingerprints:
            continue
        topology = json_key(sorted(sorted(m['unit_id'] for ref in chain['hypothesis_refs'] for m in lookup[ref]['members']) for chain in plan['chains']))
        if topology_counts.get(topology, 0) >= 4:
            continue
        topology_counts[topology] = topology_counts.get(topology, 0)+1
        fingerprints.add(key); selected.append({'partition_ref': f'P{len(selected)+1:03d}', **plan})
        if len(selected) >= max_plans:
            break
    return selected


def json_key(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def expand_partition_selection(obj, context):
    """A model must explicitly select a frozen plan AND bind every chain."""
    plan = next((p for p in context['partition_proposals'] if p['partition_ref'] == obj.get('partition_ref')), None)
    if plan is None:
        raise ValueError('partition_ref不在冻结候选方案内')
    bindings = obj.get('chain_bindings')
    if not isinstance(bindings, list):
        raise ValueError('缺少chain_bindings')
    chains = {c['chain_ref']: c for c in plan['chains']}; seen = set(); tracks = {}
    for row in bindings:
        ref = row.get('chain_ref'); target = row.get('target_ref')
        if ref not in chains or ref in seen or not isinstance(target, str):
            raise ValueError('chain_bindings引用越界、重复或目标无效')
        seen.add(ref); tracks.setdefault(target, []).extend(chains[ref]['hypothesis_refs'])
    if seen != set(chains):
        raise ValueError('chain_bindings没有覆盖选中方案的所有链')
    direct = {'snapshot_version': obj.get('snapshot_version'),
        'tracks': [{'target_ref': target, 'hypothesis_refs': refs} for target, refs in tracks.items()],
        'unassigned_units': plan['unassigned_units']}
    return expand_model_selection(direct, context)


def expand_model_selection(obj, context):
    """Expand ONLY explicit model references. No implicit best-card selection."""
    cards = {c['hypothesis_ref']: c for c in context['candidate_evidence']['hypotheses']}
    units = {u['unit_id']: u for u in context['current_units']+context['history_units']}
    if obj.get('snapshot_version') != context['snapshot_version'] or type(obj.get('snapshot_version')) is not int:
        raise ValueError('模型候选选择的快照版本不匹配')
    tracks = obj.get('tracks'); unassigned = obj.get('unassigned_units')
    if not isinstance(tracks, list) or not isinstance(unassigned, list):
        raise ValueError('tracks/unassigned_units必须是数组')
    claimed = {}; seen_targets = set()
    for track in tracks:
        ref = track.get('target_ref')
        if not isinstance(ref, str) or ref in seen_targets:
            raise ValueError('候选选择目标编号重复或无效')
        seen_targets.add(ref)
        refs = track.get('hypothesis_refs', []); points = track.get('extra_points', [])
        if not isinstance(refs, list) or not isinstance(points, list) or any(r not in cards for r in refs):
            raise ValueError('模型引用了不存在的数值候选')
        for member in [*(m for r in refs for m in cards[r]['members']), *points]:
            uid = member.get('unit_id'); branch = member.get('branch_ref')
            if uid not in units or not any(c['branch_ref'] == branch for c in units[uid]['candidates']):
                raise ValueError('模型引用了不存在的组或镜像分支')
            row = {'unit_id': uid, 'target_ref': ref, 'branch_ref': branch, 'reason_code': 'continuity'}
            if uid in claimed and claimed[uid] != row:
                old = claimed[uid]
                raise ValueError(f'模型候选选择重复占用组{uid}：{old["target_ref"]}/{old["branch_ref"]}与{ref}/{branch}；选择互斥候选不能同时使用')
            claimed[uid] = row
    for uid in unassigned:
        if uid not in units or uid in claimed:
            raise ValueError('未归属引用越界或与轨迹重复占用')
        claimed[uid] = {'unit_id': uid, 'target_ref': None, 'branch_ref': None, 'reason_code': 'insufficient'}
    if set(claimed) != set(units):
        raise ValueError('模型候选选择漏掉组：'+','.join(sorted(set(units)-set(claimed))))
    current = {u['unit_id'] for u in context['current_units']}
    return {'snapshot_version': obj['snapshot_version'],
        'assignments': [r for uid, r in claimed.items() if uid in current],
        'revisions': [r for uid, r in claimed.items() if uid not in current]}
