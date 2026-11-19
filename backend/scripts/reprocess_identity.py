"""Replay saved evidence/model text locally; never instantiate an API gateway request."""
import json
from pathlib import Path
import shutil
import sys

from backend.app.core.paths import OUTPUTS_DIR
from backend.app.core.exceptions import ResultNotFound
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.association.target_count import link_tracklets,count_hypothesis
from backend.app.agents.sonar_agent import SonarAgent
from backend.app.agents.progress import LABELS


def reprocess(source_id,root=OUTPUTS_DIR):
    repo=ResultRepository(root)
    def load(name):
        path=repo.file(source_id,name)
        if path.stat().st_size>16*1024*1024:raise ValueError('历史证据文件超过16MiB限制')
        return json.loads(path.read_text(encoding='utf-8'))
    evidence=load('candidate_evidence.json');context=load('calibration_context.json')
    rules=load('knowledge_sources.json')
    if not context.get('require_spatial') or not context.get('platforms'):raise ValueError('源结果缺少空间定位标定上下文')
    answer_path=repo.file(source_id,'answer.txt')
    if answer_path.stat().st_size>16*1024*1024:raise ValueError('历史正文过大')
    answer=answer_path.read_text(encoding='utf-8')
    sources={}
    required=['answer.txt','candidate_evidence.json','calibration_context.json','knowledge_sources.json']
    required += [f"evidence_{im['image_index']}.png" for im in evidence['images']]
    for name in required:sources[name]=repo.file(source_id,name)
    for name in ('response.json','request_meta.json','stream_state.json'):
        try:sources[name]=repo.file(source_id,name)
        except ResultNotFound:pass
    run=repo.create_run()
    for name,path in sources.items():shutil.copy2(path,run/name)
    fragments=link_tracklets(evidence);hypothesis=count_hypothesis(fragments)
    (run/'tracklets.json').write_text(json.dumps({'tracklets':fragments,'count_hypothesis':hypothesis},ensure_ascii=False,indent=2),encoding='utf-8')
    workflow=SonarAgent()
    workflow.evidence_state={'context':context,'evidence':evidence,'rules':rules,'tracklets':fragments,
                             'count_hypothesis':hypothesis,'trace':['offline_reprocess_existing_model_answer']}
    for node in ('knowledge_retrieval','pixel_candidates','tracklet_linking','count_hypothesis','association_context','model_association'):
        workflow.emit({'event':'node','node':node,'label':LABELS[node],'status':'skipped','elapsed_ms':0,
                       'summary':'此次复用已存候选与原模型回复，未重发API请求'})
    error=None
    try:workflow.render(answer,run,context)
    except (ValueError,TypeError,KeyError) as exc:error=str(exc)
    metadata=workflow.result_metadata()|{'offline_reprocessed':True,'source_result_id':source_id,'new_model_calls':0}
    (run/'execution_report.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    trace=json.loads((run/'agent_trace.json').read_text(encoding='utf-8'))
    trace.update(offline_reprocessed=True,source_result_id=source_id,model_calls_budget=0,new_model_calls=0)
    (run/'agent_trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'result_id':run.name,'source_result_id':source_id,'new_model_calls':0,
            'trajectory_generated':(run/'estimated_track.png').is_file(),'error':error}


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('用法：python -m backend.scripts.reprocess_identity 结果编号')
    print(json.dumps(reprocess(sys.argv[1]),ensure_ascii=False))
