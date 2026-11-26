"""Explicit one-call post-batch review experiment. No truth in model input."""
import copy
import json
import sys
from pathlib import Path
from dotenv import load_dotenv
from backend.app.core.config import model_settings
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.association import batch_snapshot as batch,sequential_runner as seq


def main(root):
    load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False)
    state=json.loads((root/'state.json').read_text(encoding='utf-8'));run=seq.read_run(root/'state.json',state['active_sequential_run'])
    if run['status']!='completed_awaiting_review':raise ValueError('Requires completed isolated test run')
    work=run['working'];view=seq.public_run(run)['working_view']
    # Numerical mirror candidates, with no source-prefixed IDs or chosen truth side.
    candidates=[{'unit_id':p['unit_id'],'t':p['t'],'x':p['x'],'y':p['y'],'handedness':tr['handedness'],'stable':p['stable']}
        for tr in view['spatial_preview']['tracks'] for p in tr['points']]
    context={'protocol':batch.MODE,'snapshot':batch.snapshot(work,None),'snapshot_version':work['version'],
        'platform_configuration':work['platform_config'],'batch_units':[],'worker_proposals':[],
        'position_candidates':candidates,'unit_observation_ids':{u['unit_id']:[work['aliases'][c] for c in u['candidate_ids']] for u in view['units']},
        'question':'只复核历史：检查目前暂定目标是否将同一路线在转折或时间缺口处分成多个目标。结合全部双阵观测、平台导航和几何镜像候选，若有证据则用revisions改接历史归并；不强制固定目标数，不借助强度峰宽固定值当身份。assignments为空。'}
    settings=model_settings();gateway=ConfiguredModelGateway(timeout_override=90,max_prompt_chars=PREDICTION_PROMPT_CHARS)
    (root/'review-context.json').write_text(json.dumps(context,ensure_ascii=False),encoding='utf-8')
    # The script makes exactly one new request; no retry on any failure.
    response=gateway.invoke([],batch.MERGE_INSTRUCTION.replace('\n',' ')+'\n'+json.dumps(context,ensure_ascii=False),settings,root/'review-call','data')
    (root/'review-answer.txt').write_text(response['answer'],encoding='utf-8')
    obj=batch.parse(response['answer'],work['version']);updated,revision=batch.apply(work,[],obj,run['id'],context,response.get('usage'))
    result=copy.deepcopy(run);result['working']=updated
    (root/'run-reviewed.json').write_text(json.dumps(seq.public_run(result),ensure_ascii=False),encoding='utf-8')
    report={'real_new_requests':1,'targets_before':len(work['targets']),'targets_after':len(updated['targets']),
        'revised_observations':len(revision['changes']),'reference_used_as_model_input':False,'production_state_updated':False}
    (root/'review-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':
    if '--real' not in sys.argv:raise SystemExit('--real required for paid call')
    main(Path(sys.argv[1]))
