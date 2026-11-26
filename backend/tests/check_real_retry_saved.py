"""Explicit paid stage-only recovery on a copy. At most two new merge requests."""
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
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.api.routers.incremental import read_workspace_state
from backend.app.services.association.incremental_prediction import save_state
from backend.app.services.association import batch_snapshot as batch,batch_execution as execution,batch_protocol as protocol,sequential_runner as seq


def main():
    parser=argparse.ArgumentParser();parser.add_argument('session_id');parser.add_argument('--real',action='store_true');args=parser.parse_args()
    if not args.real:raise SystemExit('--real required for at most two paid merge requests')
    if '/' in args.session_id or '\\' in args.session_id or '..' in args.session_id:raise ValueError('Invalid session')
    source=Path('outputs/mixed_prediction')/args.session_id;source_path=source/'state.json'
    root_state=read_workspace_state(source_path);rid=root_state['active_sequential_run'];original=seq.read_run(source_path,rid)
    if original['status']!='failed' or not original['steps'][-1].get('answer'):raise ValueError('Requires a returned failed merge')
    before_state=source_path.read_bytes();before_digest=seq.digest(original)
    root=Path('output/supplied-zip-test')/('compact-retry-'+time.strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:6]);root.mkdir(parents=True)
    shutil.copyfile(source_path,root/'state.json')
    with sqlite3.connect(f'file:{(source/"observations.sqlite3").as_posix()}?mode=ro',uri=True) as src,sqlite3.connect(root/'observations.sqlite3') as dest:src.backup(dest)
    path=root/'state.json';previous_snapshots=batch.snapshots(path,rid);calls=original['calls'];cursor=original['cursor']
    units,context=batch.context_for(original);wire=protocol.encode(context)
    assert protocol.decode(wire)==context
    canonical_chars=len(json.dumps(context,ensure_ascii=False,separators=(',',':')));wire_chars=len(json.dumps(wire,ensure_ascii=False,separators=(',',':')))
    remaining=len(original['spec']['queue'])-cursor-len(units)
    total=calls+remaining+execution.math_ceil_batches(remaining,original['spec']['batch_size'])+2-execution.used_retries(original)
    execution.prepare_retry(path,rid,read_workspace_state,save_state,cursor,original['spec']['context_digest'],total,2,2,True)
    load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False);settings=model_settings()
    gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    print(json.dumps({'phase':'merge_only_retry','reused_worker_replies':len(original['steps'][-1]['worker_results']),
        'prior_successful_groups':cursor,'maximum_new_model_calls':2,'canonical_chars':canonical_chars,'compact_chars':wire_chars}),flush=True)
    batch.execute(path,rid,cursor,settings,gateway,read_workspace_state,save_state)
    result=seq.read_run(path,rid);public=seq.public_run(result)
    (root/'run.json').write_text(json.dumps(public,ensure_ascii=False),encoding='utf-8')
    after_snapshots=batch.snapshots(path,rid)
    report={'source_session':args.session_id,'new_model_requests':result['calls']-calls,'status':result['status'],
        'processed_before':cursor,'processed_after':result['cursor'],'target_hypotheses':len(result['working']['targets']),
        'worker_requests_repeated':0,'snapshot_count':len(after_snapshots),'prior_snapshots_preserved':after_snapshots[:len(previous_snapshots)]==previous_snapshots,
        'canonical_chars':canonical_chars,'compact_chars':wire_chars,'input_reduction_percent':round((1-wire_chars/canonical_chars)*100,2),
        'last_error':result.get('last_error'),'production_state_unchanged':source_path.read_bytes()==before_state and seq.digest(seq.read_run(source_path,rid))==before_digest}
    assert 0<=report['new_model_requests']<=2
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_path':str(root/'report.json'),**report},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
