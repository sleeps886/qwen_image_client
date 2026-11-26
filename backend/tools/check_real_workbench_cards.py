"""Real integration test through the actual durable workbench executor, max116."""
import argparse
import json
import math
import shutil
import sqlite3
from pathlib import Path
import time
import uuid
from backend.app.core.config import model_settings
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.association import batch_snapshot as batch, sequential_runner as seq
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association.mixed_prediction import create_mixed
from backend.app.services.inputs.observation_import import import_observations
from backend.tools.check_real_joint_spatial import prepare_units, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--real', action='store_true')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--resume-isolated', type=Path)
    parser.add_argument('--acknowledge-unknown-charge-risk', action='store_true')
    parser.add_argument('--total-call-limit', type=int, help='Explicitly change total budget only for a manually authorized test copy')
    args = parser.parse_args()
    if not args.real:
        raise SystemExit('Requires --real and explicit paid authorization')
    settings = model_settings()
    if not settings.api_key:
        raise ValueError('No configured model')
    anonymous, lineage, platform, origin = prepare_units(all_groups=args.all)
    maximum = len(anonymous)+math.ceil(len(anonymous)/50)+14
    dataset = import_observations([(f'btr_target_00{i}.zip', Path(f'C:/Users/LENOVO/AppData/Local/Temp/btr_target_00{i} (2).zip').read_bytes()) for i in (1, 2, 3)])
    work = create_mixed(dataset, max_calls=maximum)
    units = [lineage[u['unit_id']]['candidate_ids'] for u in anonymous]
    spec = seq.preview(work, units, order='received', call_limit=maximum, model_fingerprint=seq.model_reference(settings), model_label=settings.model)
    spec.pop('context_digest'); spec.pop('model_calls')
    batch.configure(spec, work, batch.MODE, 50, 50, 2, 14, True, True)
    spec.update(context_digest=seq.digest(spec), model_calls=0)
    root = Path('output/supplied-zip-test')/('real-workbench-cards-'+time.strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:6]); root.mkdir(parents=True)
    path = root/'state.json'; start_cursor = 0; authorization = None
    if args.resume_isolated:
        source = args.resume_isolated.resolve(); allowed = Path('output/supplied-zip-test').resolve()
        if not source.is_relative_to(allowed) or not args.acknowledge_unknown_charge_risk:
            raise ValueError('Only isolated output copies and explicit duplicate-charge acknowledgment allowed')
        source_state = json.loads((source/'state.json').read_text(encoding='utf-8'))
        with sqlite3.connect(f'file:{(source/"observations.sqlite3").as_posix()}?mode=ro', uri=True) as src:
            original = json.loads(src.execute('SELECT payload FROM sequential_runs WHERE id=?', (source_state['active_sequential_run'],)).fetchone()[0])
            if original['status'] != 'result_unknown' or original['spec'].get('spatial_association_contract') != 'model-selected-spatial-cards-v1':
                raise ValueError('Requires a stopped isolated unknown-outcome spatial-card run')
            with sqlite3.connect(root/'observations.sqlite3') as dest:
                src.backup(dest)
        if len(original['spec']['queue']) != len(anonymous) or original['spec']['model_fingerprint'] != seq.model_reference(settings):
            raise ValueError('Dataset scope/model changed, no paid request sent')
        shutil.copyfile(source/'state.json', path)
        run = seq.read_run(path, source_state['active_sequential_run']); start_cursor = run['cursor']
        _, frozen = batch.context_for(run)
        if seq.digest(frozen) != seq.digest(run['steps'][-1]['context']):
            raise ValueError('Frozen context changed, do not reuse saved proposals')
        unknown = [r['unit_id'] for r in run['steps'][-1]['worker_results'] if r['status'] == 'result_unknown']
        if not unknown:
            raise ValueError('No unknown worker stage to explicitly retest')
        authorization = {'source_test_copy': str(source), 'duplicate_charge_risk_acknowledged': True,
            'manual_test_reissue_units': unknown, 'prior_calls': run['calls'], 'total_call_limit': run['spec']['call_limit'],
            'automatic_unknown_retry_enabled': False}
        if args.total_call_limit is not None:
            if not run['calls'] < args.total_call_limit <= 10000:
                raise ValueError('Invalid explicit test budget')
            authorization['previous_total_call_limit']=run['spec']['call_limit']
            authorization['new_total_call_limit']=args.total_call_limit
            authorization['total_call_limit']=args.total_call_limit
            run['spec']['call_limit']=args.total_call_limit
            base=run['spec']['base_model_call_upper_bound']
            run['spec']['retry_policy']['budget']=args.total_call_limit-base
            run['spec']['model_call_upper_bound']=args.total_call_limit
            spec_digest=dict(run['spec']);spec_digest.pop('context_digest',None);spec_digest.pop('model_calls',None)
            run['spec']['context_digest']=seq.digest(spec_digest)
            source_state['max_calls']=max(source_state['max_calls'],args.total_call_limit)
        for attempt in run['steps'][-1]['attempts']:
            if attempt['status'] == 'result_unknown':
                attempt['manual_retest_authorization'] = authorization
                attempt['error_code'] = 'explicit_manual_retest_unknown_outcome'
        run.update(status='paused', resume_batch=True, pause_requested=False)
        run.setdefault('manual_test_authorizations', []).append(authorization)
        seq.write_run(path, run); source_state['status'] = 'sequential_paused'; save_state(path, source_state)
        maximum = run['spec']['call_limit']
        (root/'manual-retest-authorization.json').write_text(json.dumps(authorization, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        run = seq.create_run(path, work, spec, 'explicit-real-workbench-card-test')
        work.update(active_sequential_run=run['id'], version=run['root_version'], status='sequential_prepared'); save_state(path, work)
    gateway = ConfiguredModelGateway(timeout_override=300, max_prompt_chars=PREDICTION_PROMPT_CHARS)
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    print(json.dumps({'phase': 'start', 'root': str(root), 'model': settings.model, 'maximum_requests': maximum}), flush=True)
    start = time.perf_counter()
    for cursor in range(start_cursor, len(anonymous), 50):
        print(json.dumps({'phase': 'actual_workbench_dispatch', 'cursor': cursor}), flush=True)
        batch.execute(path, run['id'], cursor, settings, gateway, read, save_state)
        current = seq.read_run(path, run['id']); public = seq.public_run(current)
        (root/'public-run.json').write_text(json.dumps(public, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'phase': 'result', 'processed': current['cursor'], 'status': current['status'], 'calls': current['calls'], 'error': current.get('last_error')}, ensure_ascii=True), flush=True)
        if current['status'] not in ('paused', 'completed_awaiting_review'):
            break
    current = seq.read_run(path, run['id']); assignments = {}; choices = current['working'].get('batch_position_choices', {})
    for u in anonymous[:current['cursor']]:
        ids = lineage[u['unit_id']]['candidate_ids']
        assignments[u['unit_id']] = {'unit_id': u['unit_id'], 'target_ref': current['working'].get('batch_assignments', {}).get(ids[0]),
            'branch_ref': choices.get(ids[0]), 'reason_code': 'continuity'}
    report = evaluate(root, anonymous[:current['cursor']], assignments, lineage, origin, platform)
    report.update(real_model_requests=current['calls'], status=current['status'], snapshots=len(batch.snapshots(path, run['id'])),
        last_error=current.get('last_error'), elapsed_seconds=round(time.perf_counter()-start, 2), actual_workbench_executor=True,
        results_adopted=False, production_session_modified=False, model=settings.model,
        manual_unknown_retest_authorization=authorization, new_requests_in_this_phase=current['calls']-(authorization['prior_calls'] if authorization else 0))
    (root/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report_path': str(root/'report.json'), **report}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
