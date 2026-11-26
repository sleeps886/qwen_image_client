"""Read-only, post-inference spatial audit. Reference never enters model/state.

Usage: python -m backend.tools.audit_spatial_run SESSION --mirror-side left
The production SQLite file is opened mode=ro; no paid requests are made.
"""
import argparse
import collections
import itertools
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import zipfile

from backend.app.services.association import sequential_runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session')
    parser.add_argument('--mirror-side', choices=['left', 'right'], default='left')
    parser.add_argument('--archives', type=Path, default=Path('C:/Users/LENOVO/AppData/Local/Temp'))
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.session):
        raise ValueError('Invalid session ID')
    source = Path('outputs/mixed_prediction') / args.session
    raw_state = (source / 'state.json').read_bytes()
    state = json.loads(raw_state)
    rid = state['active_sequential_run']
    with sqlite3.connect(f'file:{(source / "observations.sqlite3").resolve().as_posix()}?mode=ro', uri=True) as db:
        payload = db.execute('SELECT payload FROM sequential_runs WHERE id=?', (rid,)).fetchone()[0]
    run = json.loads(payload)
    view = sequential_runner.public_run(run)['working_view']
    out = Path('output/supplied-zip-test') / ('spatial-audit-' + args.session)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'view.json').write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
    script = """
const fs=require('fs'),{spatialDisplayGroups}=require('./frontend/point-sampling.js');
const v=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const d=spatialDisplayGroups(v.spatial_preview,v.units,v.points,{mirrorSide:process.argv[3],
 edges:v.continuation_graph.edges,membership:v.membership,autoConnect:true,modelOnly:true});
fs.writeFileSync(process.argv[2],JSON.stringify(d));
"""
    subprocess.run(['node', '-e', script, str(out / 'view.json'), str(out / 'display.json'), args.mirror_side], check=True)
    display = json.loads((out / 'display.json').read_text(encoding='utf-8'))
    # Source catalogue and reference are loaded ONLY now, for independent scoring.
    origin = view['spatial_preview']['origin_lat_lon']
    truth = {}
    for index in (1, 2, 3):
        with zipfile.ZipFile(args.archives / f'btr_target_00{index} (2).zip') as archive:
            reference = json.loads(archive.read('evaluation/reference.json'))
        truth[f'I0{index}'] = {p['t']: (
            6371000 * math.cos(math.radians(origin[0])) * math.radians(p['longitude'] - origin[1]),
            6371000 * math.radians(p['latitude'] - origin[0])) for p in reference['points']}
    groups = {g['unit_id']: g for g in display['groups']}
    claims = collections.defaultdict(list)
    confusion = collections.defaultdict(collections.Counter)
    selected_errors, nearest_errors, samples = [], [], []
    for group in groups.values():
        if not group['validated']:
            continue
        cid = group['candidate_ids'][0]
        source_id = cid.split('-')[0]
        membership = view['membership'][cid]
        target = membership.get('target_id')
        confusion[target][source_id] += 1
        if target:
            claims[target, group['t']].append(group['unit_id'])
        if group['selected']:
            reference = truth[source_id][group['t']]
            distance = lambda candidate: math.dist((candidate['point']['x'], candidate['point']['y']), reference)
            selected_errors.append(distance(group['selected']))
            nearest_errors.append(min(map(distance, group['candidates'])))
            samples.append((group, source_id, reference))
    edges = []
    for edge in display['display_links']:
        a, b = groups[edge['from']], groups[edge['to']]
        pa, pb = a['selected']['point'], b['selected']['point']
        edges.append({'from': a['unit_id'], 'to': b['unit_id'],
            'different_reference_source': a['candidate_ids'][0].split('-')[0] != b['candidate_ids'][0].split('-')[0],
            'apparent_speed_mps': math.dist((pa['x'], pa['y']), (pb['x'], pb['y'])) / (b['t'] - a['t'])})
    collisions = [{'target': target, 't': t, 'units': ids}
        for (target, t), ids in claims.items() if len(ids) > 1]
    targets = sorted(t for t in confusion if t)
    correct = max((sum(confusion[t][s] for t, s in zip(targets, order))
        for order in itertools.permutations(truth, len(targets))), default=0) if len(targets) <= 3 else None
    rmse = lambda values: math.sqrt(sum(x*x for x in values)/len(values)) if values else None
    report = {'evaluation_only': True, 'reference_used_for_model_or_state': False,
        'session': args.session, 'run': rid, 'status': run['status'], 'calls': run['calls'],
        'validated_groups': run['cursor'], 'pending_groups': len(run['spec']['queue'])-run['cursor'],
        'mirror_side': args.mirror_side, 'confusion': dict(confusion),
        'best_one_to_one_correct_groups': correct,
        'same_time_target_collisions': collisions, 'rendered_edges': len(edges),
        'cross_reference_source_edges': sum(e['different_reference_source'] for e in edges),
        'max_apparent_speed_mps': max((e['apparent_speed_mps'] for e in edges), default=0),
        'selected_position_rmse_m': rmse(selected_errors),
        'nearest_candidate_rmse_m_evaluation_only': rmse(nearest_errors),
        'selected_positions_over_100m_from_reference': sum(e > 100 for e in selected_errors),
        'production_state_unchanged': (source/'state.json').read_bytes() == raw_state,
        'real_model_calls': 0}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'edges.json').write_text(json.dumps(edges, indent=2), encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(17, 6), facecolor='#0b1020')
    colors = ['#48cbb4', '#edb459', '#c58eeb']
    for ax in axes:
        ax.set_facecolor('#0b1020'); ax.tick_params(colors='#b9ccdf')
        ax.set_aspect('equal', adjustable='datalim'); ax.grid(alpha=.15)
        ax.set_xlabel('East (m)', color='white'); ax.set_ylabel('North (m)', color='white')
        ax.scatter([0], [0], marker='s', color='#ffdf50', s=25)
    for source_id, color in zip(truth, colors):
        path = list(truth[source_id].values())
        axes[0].plot([q[0] for q in path], [q[1] for q in path], color=color, label=source_id)
        path = []
        for group, source_key, reference in samples:
            if source_key != source_id:
                continue
            candidate = min(group['candidates'], key=lambda c: math.dist((c['point']['x'], c['point']['y']), reference))
            path.append((group['t'], candidate['point']))
        path.sort(key=lambda item: item[0])
        axes[2].plot([q['x'] for _, q in path], [q['y'] for _, q in path], color=color)
    for track in display['tracks']:
        ps = track['points']
        target = view['membership'][ps[0]['candidate_ids'][0]].get('target_id')
        color = colors[targets.index(target) % len(colors)] if target in targets else '#777777'
        axes[1].scatter([q['x'] for q in ps], [q['y'] for q in ps], s=6, edgecolors=color, facecolors='none')
        for a, b in zip(ps, ps[1:]):
            if b['connect_previous']:
                axes[1].plot([a['x'], b['x']], [a['y'], b['y']], color=color, lw=.7)
    for ax, title in zip(axes, ('Reference (evaluation only)', f'Current model + {args.mirror_side} mirror display', 'Reference-nearest candidate (evaluation ONLY)')):
        ax.set_title(title, color='white')
    axes[0].legend(); fig.tight_layout(); fig.savefig(out/'comparison.png', dpi=140); plt.close(fig)
    print(json.dumps({k: value for k, value in report.items() if k != 'same_time_target_collisions'}, ensure_ascii=False))
    print(str(out.resolve()))


if __name__ == '__main__':
    main()
