"""Manual, one-call-per-step prediction API; existing pipelines remain unchanged."""
import json
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter,Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field

from backend.app.core.config import model_settings
from backend.app.core.exceptions import BusyError,InputError,ModelServiceError,NotFoundError
from backend.app.core.paths import OUTPUTS_DIR,STATIC_DIR
from backend.app.core.runtime import SLOTS
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.association.incremental_prediction import (
    apply_answer,create_state,prepare_round,public_state,save_state)

router=APIRouter(tags=['incremental prediction'])
ROOT=OUTPUTS_DIR/'incremental'
LOCK=threading.Lock()


def get_prediction_gateway():
    return ConfiguredModelGateway(timeout_override=90)


class CreatePrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:dict
    seed_ids:list[str]=Field(min_length=3,max_length=5)
    stride:Annotated[int,Field(strict=True,ge=1,le=8)]=1
    horizon:Annotated[float,Field(gt=0,le=600,allow_inf_nan=False)]=20
    max_calls:Annotated[int,Field(strict=True,ge=1,le=100)]=20


class AdvancePrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    consent:bool=False


def session_path(session_id):
    if not ResultRepository.valid_id(session_id):raise NotFoundError('任务不存在')
    directory=ROOT/session_id;path=directory/'state.json'
    if ROOT.is_symlink() or directory.is_symlink() or path.is_symlink() or not path.is_file():raise NotFoundError('任务不存在')
    return path


@router.get('/incremental',include_in_schema=False)
def prediction_page():return FileResponse(STATIC_DIR/'incremental.html')


@router.post('/api/incremental/sessions')
def create_prediction(body:CreatePrediction):
    try:state=create_state(body.dataset,body.seed_ids,body.stride,body.horizon,body.max_calls)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise InputError('候选数据或种子无效，请检查格式、编号、阵列和时间') from exc
    directory=ResultRepository(ROOT).create_run()
    save_state(directory/'state.json',state)
    return {'session_id':directory.name,**public_state(state)}


@router.get('/api/incremental/sessions/{session_id}')
def get_prediction(session_id:str):
    state=json.loads(session_path(session_id).read_text(encoding='utf-8'))
    return {'session_id':session_id,**public_state(state)}


@router.post('/api/incremental/sessions/{session_id}/step')
def advance_prediction(session_id:str,body:AdvancePrediction,gateway=Depends(get_prediction_gateway)):
    if not body.consent:raise InputError('每次推进须同意发送点位数据及API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置后端模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError('已有增量预测轮次执行中，请等待，不要重复提交')
    acquired=False
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=session_path(session_id);state=json.loads(path.read_text(encoding='utf-8'))
        if body.version!=state['version']:raise InputError('状态版本已变化，请重新加载任务')
        try:context,prompt=prepare_round(state)
        except ValueError as exc:raise InputError(str(exc)) from None
        # Durable intent precedes the cloud call. A crash/timeout cannot silently replay it.
        state['calls']+=1;state['status']='pending'
        save_state(path,state)
        attempt=path.parent/f"attempt_{state['calls']:03d}.json"
        save_state(attempt,{'status':'pending','input':context,'prompt':prompt})
        try:
            response=gateway.invoke([],prompt,settings,path.parent/'model_calls','data')
        except Exception:
            state['status']='call_unknown';save_state(path,state)
            save_state(attempt,{'status':'call_unknown','input':context,'automatic_retry':False})
            raise ModelServiceError('模型调用失败或结果未知；未自动重试，本任务已停止，可能已计费') from None
        answer=response.get('answer','');usage=response.get('usage')
        save_state(attempt,{'status':'returned','input':context,'answer':answer,'usage':usage})
        try:new=apply_answer(state,context,answer,usage)
        except (ValueError,TypeError,KeyError,AttributeError):
            state['status']='invalid_response';save_state(path,state)
            raise ModelServiceError('模型回复未通过纯点位协议校验，已保存回复；未接入新点，未自动重试') from None
        save_state(path,new)
        return {'session_id':session_id,**public_state(new)}
    finally:
        if acquired:SLOTS.release()
        LOCK.release()
