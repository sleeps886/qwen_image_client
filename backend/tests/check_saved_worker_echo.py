"""Reuse the user's saved responses on an isolated copy; at most 1 paid merge.

No worker is reissued. No production state is modified. --real is mandatory.
"""
import argparse
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
from backend.app.core.config import model_settings
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS
from backend.app.api.routers.incremental import read_workspace_state
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import batch_snapshot as batch,sequential_runner as seq
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway


def main():
    parser=argparse.ArgumentParser();parser.add_argument('session_id');parser.add_argument('--real',action='store_true');args=parser.parse_args()
    if not args.real:raise SystemExit('--real required for one paid merge')
    # Session is a leaf, never a computed arbitrary filesystem path.
    if '/' in args.session_id or '\\' in args.session_id or '..' in args.session_id:raise ValueError('Invalid session id')
    source=Path('outputs/mixed_prediction')/args.session_id
    original=(source/'state.json').read_bytes()
    state=read_workspace_state(source/'state.json');rid=state['active_sequential_run']
    run=seq.read_run(source/'state.json',rid)
    if run['status']!='failed' or run['steps'][-1].get('answer'):raise ValueError('Requires a failed complete worker batch without a merge call')
    baseline=seq.digest(run);calls=run['calls']
    root=Path('output/supplied-zip-test')/('worker-echo-'+time.strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:6]);root.mkdir(parents=True)
    shutil.copyfile(source/'state.json',root/'state.json')
    with sqlite3.connect(f'file:{(source/"observations.sqlite3").as_posix()}?mode=ro',uri=True) as src,sqlite3.connect(root/'observations.sqlite3') as dest:src.backup(dest)
    path=root/'state.json';recovered=batch.recover_saved_proposals(path,rid,read_workspace_state)
    assert recovered['calls']==calls and recovered['resume_merge']
    load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False)
    settings=model_settings();gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    print(json.dumps({'phase':'merge_only','reused_worker_replies':len(recovered['steps'][-1]['worker_results']),'maximum_new_calls':1}),flush=True)
    batch.execute(path,rid,recovered['cursor'],settings,gateway,read_workspace_state,save_state)
    result=seq.read_run(path,rid);public=seq.public_run(result)
    (root/'run.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['calls']-calls<=1
    report={'source_session':args.session_id,'new_model_calls':result['calls']-calls,'worker_requests_repeated':0,
        'status':result['status'],'processed_groups':result['cursor'],'snapshots':len(batch.snapshots(path,rid)),
        'target_hypotheses':len(result['working']['targets']),'last_error':result.get('last_error'),
        'warnings':result['steps'][-1].get('warnings',[]),
        'production_state_unchanged':(source/'state.json').read_bytes()==original and seq.digest(seq.read_run(source/'state.json',rid))==baseline}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_path':str(root/'report.json'),**report},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
