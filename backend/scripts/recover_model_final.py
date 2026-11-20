"""Revalidate a saved model answer into a new result. No gateway or paid calls."""
import argparse
import json
import shutil
from backend.app.agents.model_trajectory import validate_final
from backend.app.core.paths import OUTPUTS_DIR
from backend.app.repositories.result_repository import ResultRepository,RESULT_FILES
from backend.app.services.rendering.model_views import render_model_views


def recover(result_id, output_root=OUTPUTS_DIR):
    repo=ResultRepository(output_root)
    # Repository ID/path checks reject traversal, symlinks and missing files.
    answer=repo.file(result_id,'model_final_answer.txt').read_text(encoding='utf-8')
    context=json.loads(repo.file(result_id,'calibration_context.json').read_text(encoding='utf-8'))
    candidates=json.loads(repo.file(result_id,'backend_tracks.json').read_text(encoding='utf-8'))
    document=validate_final(answer,context,candidates)
    previous=json.loads(repo.file(result_id,'execution_report.json').read_text(encoding='utf-8'))
    run=repo.create_run()
    source=repo.file(result_id,'model_final_answer.txt').parent
    for name in RESULT_FILES:
        if name in ('tracks.json','estimated_track.png','execution_report.json','response.json','model_final_report.json'):continue
        file=source/name
        if file.is_file() and not file.is_symlink():shutil.copyfile(file,run/name)
    document.update(new_model_calls=0,offline_reprocessed=True,source_result_id=result_id)
    render_model_views(document,run,context,candidates)
    (run/'answer.txt').write_text(answer,encoding='utf-8')
    report={'status':'generated','coordinate_source':'saved_model_output','physical_validation':'not_performed',
            'recovered_offline':True,'new_model_calls':0,'automatic_retry':False,'identity_audit':document['model_identity_audit']}
    metadata={**previous,'source_node_events':previous.get('node_events',[]),'node_events':[],
              'result_source':'model_visual_fusion','answer_source':'model_output','model_final_requested':True,'model_final':report,
              'new_model_calls':0,'offline_reprocessed':True,'source_result_id':result_id}
    metadata['target_count']={**metadata.get('target_count',{}),
                              'localized_target_count':len({t['id'] for t in document['tracks']})}
    repo.save(run,'execution_report.json',metadata)
    repo.save(run,'model_final_report.json',report)
    repo.save(run,'response.json',{'result_source':'model_visual_fusion','new_model_calls':0,'offline_reprocessed':True})
    return {'result_id':run.name,'source_result_id':result_id,'new_model_calls':0,'trajectory_image':str((run/'estimated_track.png').resolve())}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('result_id')
    print(json.dumps(recover(parser.parse_args().result_id),ensure_ascii=False))


if __name__=='__main__':main()
