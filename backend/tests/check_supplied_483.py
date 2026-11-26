"""Opt-in supplied-ZIP scale test. Offline gateway; temporary state only.

Run: python -m backend.tests.check_supplied_483
Not an identity-accuracy evaluation. Intentionally retains all points unassigned.
"""
import copy
import json
import tempfile
import time
from pathlib import Path

from backend.app.models.model_settings import ModelSettings
from backend.app.services.inputs.observation_import import import_observations
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import sequential_runner as seq
from backend.app.services.association.sequential_spatial_preview import spatial_preview


class OfflineGateway:
    calls = 0
    def invoke(self, images, prompt, *args):
        self.calls += 1
        context = json.loads(prompt.split('\n', 1)[1])
        # No external service, no source labels used to decide identity.
        return {'answer': json.dumps({
            'step_id': context['step_id'],
            'base_state_version': context['base_state_version'],
            'tracks': [],
            'unassigned': [p['observation_id'] for p in context['observations']],
        })}


def main():
    started = time.perf_counter()
    out = Path('output/supplied-zip-test/full-483')
    out.mkdir(parents=True, exist_ok=True)
    files = [Path(f'C:/Users/LENOVO/AppData/Local/Temp/btr_target_00{i} (2).zip') for i in (1, 2, 3)]
    dataset = import_observations([(p.name, p.read_bytes()) for p in files])
    state = create_mixed(dataset, max_calls=20)
    buckets = {}
    lookup = {p['candidate_id']: p for p in state['points']}
    for source in state['browse_catalog']:
        for cid in source['candidate_ids']:
            p = lookup[cid]
            buckets.setdefault((source['target_id'], p['platform_id'], p['t']), []).append(cid)
    units = list(buckets.values())
    assert len(units) == 483 and len(lookup) == 966
    assert all(len(ids) == 2 and {lookup[c]['array'] for c in ids} == {'hull', 'tow'} for ids in units)
    errors = {}
    for budget in (20, 482):
        try:
            seq.preview(state, units, call_limit=budget)
        except ValueError as error:
            errors[str(budget)] = str(error)
    assert len(errors) == 2, 'Current low-budget session unexpectedly accepted full queue'
    # Test-only explicit service budget, NOT an API/UI limit change.
    state['max_calls'] = 482
    prep = time.perf_counter()
    spec = seq.preview(state, units, call_limit=482)
    prep_seconds = time.perf_counter() - prep
    gateway = OfflineGateway()
    settings = ModelSettings('offline-scale-test', 'http://invalid.local', 'offline-not-a-secret')
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    durations = []
    blocked = None
    with tempfile.TemporaryDirectory(prefix='xiaoguai-483-') as directory:
        path = Path(directory) / 'state.json'
        save_state(path, state)
        run = seq.create_run(path, state, spec, 'offline-full-483')
        root = copy.deepcopy(state)
        root.update(active_sequential_run=run['id'], version=run['root_version'], status='sequential_prepared')
        save_state(path, root)
        for i in range(483):
            tick = time.perf_counter()
            try:
                seq.execute_one(path, run['id'], i, settings, gateway, read, save_state)
            except ValueError as error:
                blocked = {'step':i+1, 'reason':str(error)}
                break
            saved = seq.read_run(path, run['id'])
            assert saved['cursor'] == i + 1, saved.get('last_error')
            context = saved['steps'][-1]['context']
            allowed = {state['aliases'][cid] for u in spec['queue'][:i+1] for cid in u['candidate_ids']}
            mentioned = {p['observation_id'] for p in context['observations']}
            mentioned.update(p['observation_id'] for t in context['tracks'].values() for p in t['history'])
            assert mentioned <= allowed, 'Future measurement leaked'
            durations.append(time.perf_counter() - tick)
            if i + 1 in (20, 160, 161, 483):
                public = seq.public_run(saved)
                (out / f'view-{i+1}.json').write_text(json.dumps(public['working_view'], ensure_ascii=False), encoding='utf-8')
            if (i + 1) % 25 == 0:
                print(json.dumps({'processed':i+1,'elapsed_seconds':round(time.perf_counter()-started,2)}), flush=True)
        public = seq.public_run(saved)
        view = public['working_view']
        assert blocked or saved['status'] == 'completed_awaiting_review'
        assert len(view['units']) == saved['cursor'] and len(view['points']) == 2*saved['cursor']
        assert read(path)['tracks'] == {}, 'Provisional result adopted without confirmation'
        spatial = view['spatial_preview']
        accounted = {p['unit_id'] for t in spatial['tracks'] for p in t['points']}
        accounted.update(p['unit_id'] for p in spatial['unavailable'])
        assert {u['unit_id'] for u in view['units']} <= accounted
        assert gateway.calls == saved['calls'] == saved['cursor']-1
    # Independent full-size numerical/rendering fixture, not a completed run.
    # Validated flag is synthetic here: this proves coverage/performance only.
    fixture = {'points':state['points'],
        'units':[{**u,'validated':True} for u in spec['queue']],
        'membership':{cid:{'status':'unassigned','fragment_id':None,'target_id':None} for cid in lookup},
        'continuation_graph':{'edges':[]}}
    tick = time.perf_counter()
    fixture['spatial_preview'] = spatial_preview(fixture,state['platform_config'])
    geometry_seconds = time.perf_counter()-tick
    (out/'geometry-fixture-483.json').write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf-8')
    report = dict(groups=483, raw_observations=966, status=saved['status'],
        processed_groups=saved['cursor'],blocked=blocked,
        fake_calls=gateway.calls, real_model_calls=0, budget_rejections=errors,
        preparation_seconds=round(prep_seconds,4), elapsed_seconds=round(time.perf_counter()-started,2),
        last_step_seconds=round(durations[-1],3), max_step_seconds=round(max(durations),3),
        full_geometry_fixture_seconds=round(geometry_seconds,3),
        full_geometry_fixture_is_model_result=False,
        future_measurements_leaked=False, silently_missing_processed_groups=0,
        accuracy_evaluated=False, production_session_modified=False)
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
