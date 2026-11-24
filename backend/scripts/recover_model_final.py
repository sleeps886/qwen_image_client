"""Revalidate a saved model answer into a new result. No gateway or paid calls."""
import argparse
import json
import shutil
from backend.app.agents.model_trajectory import validate_final
from backend.app.core.paths import OUTPUTS_DIR
from backend.app.repositories.result_repository import ResultRepository,RESULT_FILES
from backend.app.services.rendering.model_views import render_model_views
from backend.app.services.quality.model_final_quality import audit_geometry,require_geometry,ModelGeometryRejected


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
        if name in ('tracks.json','estimated_track.png','model_mirror_candidates.png','execution_report.json','response.json','model_final_report.json'):continue
        file=source/name
        if file.is_file() and not file.is_symlink():shutil.copyfile(file,run/name)
    document.update(new_model_calls=0,offline_reprocessed=True,source_result_id=result_id)
    fields=json.loads(repo.file(result_id,'inverse_fields.json').read_text(encoding='utf-8')) if (source/'inverse_fields.json').is_file() else {'images':[]}
    selection=json.loads(repo.file(result_id,'inverse_selection.json').read_text(encoding='utf-8')) if (source/'inverse_selection.json').is_file() else None
    quality=audit_geometry(document,candidates,fields,context,selection)
    repo.save(run,'model_final_quality.json',quality)
    accepted=True;error=None
    try:
        require_geometry(quality)
        render_model_views(document,run,context,candidates)
    except ModelGeometryRejected as exc:
        accepted=False;error=str(exc)
    (run/'answer.txt').write_text(answer,encoding='utf-8')
    report={'status':'generated' if accepted else 'failed','coordinate_source':'saved_model_output','physical_validation':'observation_consistency_only' if accepted else 'rejected',
            'geometry_audit':quality,'numeric_fallback':False,
            'recovered_offline':True,'new_model_calls':0,'automatic_retry':False,'identity_audit':document['model_identity_audit']}
    if not accepted:report.update(failed_stage='model_reprojection',error_message=error)
    metadata={**previous,'source_node_events':previous.get('node_events',[]),'node_events':[],
              'result_source':'model_visual_fusion' if accepted else 'model_visual_fusion_failed','answer_source':'model_output','model_final_requested':True,'model_final':report,
              'new_model_calls':0,'offline_reprocessed':True,'source_result_id':result_id}
    metadata['target_count']={**metadata.get('target_count',{}),
                              'localized_target_count':len({t['id'] for t in document['tracks']}) if accepted else 0}
    repo.save(run,'execution_report.json',metadata)
    repo.save(run,'model_final_report.json',report)
    repo.save(run,'response.json',{'result_source':metadata['result_source'],'new_model_calls':0,'offline_reprocessed':True})
    return {'result_id':run.name,'source_result_id':result_id,'new_model_calls':0,'status':report['status'],
            'trajectory_image':str((run/'estimated_track.png').resolve()) if accepted else None,'error_message':error}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('result_id')
    print(json.dumps(recover(parser.parse_args().result_id),ensure_ascii=False))


if __name__=='__main__':main()
