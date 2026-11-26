import json
import threading
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.association.spatial_card_protocol import decode_frame, encode_frame
from backend.tests.test_batch_snapshot import read, SETTINGS


def prepared(path):
    dataset = {'candidates': [{'candidate_id': f'SECRET-{a}-{i}', 't': i*10., 'platform_id': 'A', 'array': a,
        'bearing_deg': 45. if a == 'hull' else 42.} for i in range(8) for a in ('hull', 'tow')],
        'platform_config': {'platforms': [{'name': 'A', 'start_ll': [30, 122], 'heading_deg': 0,
            'speed_kn': 0, 'hull_m': [0, 0], 'tow_m': [-100, 0]}]}}
    state = create_mixed(dataset, max_calls=30)
    units = [[f'SECRET-{a}-{i}' for a in ('hull', 'tow')] for i in range(8)]
    spec = seq.preview(state, units, order='received', call_limit=30)
    spec.pop('context_digest'); spec.pop('model_calls')
    batch.configure(spec, state, batch.MODE, 4, 4, 2, 4, True, True)
    spec.update(context_digest=seq.digest(spec), model_calls=0)
    run = seq.create_run(path, state, spec, 'card-test')
    state.update(active_sequential_run=run['id'], version=run['root_version'], status='sequential_prepared')
    save_state(path, state)
    return run


class Gateway:
    def __init__(self):
        self.inputs = []; self.lock = threading.Lock()

    def invoke(self, images, prompt, *args):
        obj = json.loads(prompt.split('\n', 1)[1])
        if obj.get('wire_format') == 'spatial-cards-lossless-v1':obj = decode_frame(obj)
        with self.lock:
            self.inputs.append(obj)
        if 'focus_unit_id' in obj:
            answer = {'snapshot_version': obj['snapshot_version'], 'hypothesis_ref': obj['allowed_hypothesis_refs'][0], 'reason_code': 'continuity'}
        else:
            plan = obj['partition_proposals'][0]
            answer = {'snapshot_version': obj['snapshot_version'], 'partition_ref': plan['partition_ref'], 'chain_bindings': [
                {'chain_ref': chain['chain_ref'], 'target_ref': max(chain['provisional_history_target_support'],
                    key=chain['provisional_history_target_support'].get) if chain['provisional_history_target_support'] else f'N{i+1}'}
                for i, chain in enumerate(plan['chains'])]}
        return {'answer': json.dumps(answer), 'usage': {'offline_test': True}}


def test_actual_executor_freezes_model_choices_and_no_future_units(tmp_path):
    path = tmp_path/'state.json'; run = prepared(path); gateway = Gateway()
    batch.execute(path, run['id'], 0, SETTINGS, gateway, read, save_state)
    first = seq.read_run(path, run['id'])
    assert first['status'] == 'paused', first.get('last_error')
    snapshots = batch.snapshots(path, run['id'])
    assert len(snapshots) == 1 and len(snapshots[0]['selected_position_branches']) == 8
    assert len(first['working']['batch_position_choices']) == 8
    for frame in gateway.inputs:
        assert len(frame['current_units']) == 4 and frame['history_units'] == []
        assert 'SECRET-' not in json.dumps(frame)
        assert 'U000005' not in json.dumps(frame)
    batch.execute(path, run['id'], 4, SETTINGS, gateway, read, save_state)
    final = seq.read_run(path, run['id']); public = seq.public_run(final)
    assert final['status'] == 'completed_awaiting_review', final.get('last_error')
    assert final['calls'] == 10 and len(final['working']['batch_position_choices']) == 16
    assert batch.snapshots(path, run['id'])[0] == snapshots[0]
    doc = public['working_view']['spatial_preview']
    assert doc['branch_selection_source'] == 'model' and len(doc['model_branch_choices']) == 8
    assert all(branch in ('pp', 'mm') for branch in doc['model_branch_choices'].values())


def test_old_run_without_flag_is_not_upgraded(tmp_path):
    path = tmp_path/'state.json'; run = prepared(path)
    run['spec'].pop('spatial_association_contract')
    units, context = batch.context_for(run)
    assert 'spatial_frame' not in context


def test_spatial_wire_roundtrip_exact_without_dropping_facts(tmp_path):
    run = prepared(tmp_path/'state.json')
    _, context = batch.context_for(run); frame = context['spatial_frame']
    wire = encode_frame(frame)
    assert decode_frame(wire) == frame
    assert len(json.dumps(wire)) < len(json.dumps(frame))
