from fastapi import APIRouter,HTTPException
import json
from fastapi.responses import FileResponse
from backend.app.core.paths import OUTPUTS_DIR as OUTPUTS
from backend.app.core.exceptions import ResultNotFound,InvalidStoredResult
from backend.app.repositories.result_repository import ResultRepository

router=APIRouter(tags=['results'])


@router.get('/api/results/{result_id}/{filename}',summary='下载已保存的调用结果')
def result_file(result_id:str,filename:str):
    try:path=ResultRepository(OUTPUTS).file(result_id,filename)
    except ResultNotFound as exc:raise HTTPException(404,str(exc)) from None
    return FileResponse(path,filename=filename,content_disposition_type='inline' if filename.endswith('.png') else 'attachment')


@router.get('/api/history',summary='列出本地已生成的轨迹图，不调用模型')
def history():return {'items':ResultRepository(OUTPUTS).history()}


@router.get('/api/history/{result_id}',summary='载入已生成的本地结果，无需再次调用模型')
def historical_result(result_id:str):
    try:
        stored=ResultRepository(OUTPUTS).historical_result(result_id);doc=stored['document']
        metadata={}
        if 'execution_report.json' in stored['files']:
            file=ResultRepository(OUTPUTS).file(result_id,'execution_report.json')
            if file.stat().st_size<=4*1024*1024:
                try:
                    saved=json.loads(file.read_text(encoding='utf-8'))
                    if isinstance(saved,dict):metadata={k:saved[k] for k in ('node_events','target_count','offline_reprocessed','source_result_id','new_model_calls') if k in saved}
                except (OSError,ValueError):pass
        return {'ok':True,'result_id':result_id,'answer':stored['answer'],
            'input_mode':doc.get('input_mode','images'),'input_data':{'plot_kind':doc.get('plot_kind','tracks')},
            'trajectory_status':'bearing_time' if doc['coordinate_system']=='bearing_time' else 'position_estimate',
            'trajectory_image':f'/api/results/{result_id}/estimated_track.png','warnings':doc.get('warnings',[]),
            'downloads':{n:f'/api/results/{result_id}/{n}' for n in stored['files']},'usage':None if metadata.get('offline_reprocessed') else stored.get('usage'),**metadata}
    except ResultNotFound as exc:raise HTTPException(404,str(exc)) from None
    except (InvalidStoredResult,KeyError,TypeError):raise HTTPException(400,'本地结果格式无效') from None


@router.get('/api/comparisons/{result_id}/{comparison_id}/{filename}')
def comparison_file(result_id:str,comparison_id:str,filename:str):
    try:path=ResultRepository(OUTPUTS).comparison_file(result_id,comparison_id,filename)
    except ResultNotFound as exc:raise HTTPException(404,str(exc)) from None
    return FileResponse(path,filename=filename,content_disposition_type='inline' if filename.endswith('.png') else 'attachment')
