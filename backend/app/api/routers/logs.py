from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from backend.app.core.exceptions import InputError,NotFoundError
from backend.app.core.logging import LOGS

router=APIRouter(tags=["logs"])


@router.get('/api/logs/{request_id}')
def request_logs(request_id:str,after:int=0):
    if after<0:raise InputError('游标无效')
    try:return LOGS.snapshot(request_id,after)
    except KeyError:raise NotFoundError('日志尚未建立、已过期或服务已重启') from None


@router.get('/api/logs/{request_id}/download')
def download_logs(request_id:str):
    try:events=LOGS.snapshot(request_id)['events']
    except KeyError:raise NotFoundError('日志不存在') from None
    lines=[f"{e['time']} +{e['elapsed_s']:.2f}s [{e['level']}] {e['message']}" for e in events]
    return PlainTextResponse('\n'.join(lines),headers={'Content-Disposition':'attachment; filename="request.log"'})


