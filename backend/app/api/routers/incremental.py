"""Manual, one-call-per-step prediction API; existing pipelines remain unchanged."""
import json
import copy
import hashlib
import threading
import asyncio
from pathlib import Path
from typing import Annotated,Literal

from fastapi import APIRouter,Depends,File,UploadFile
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse,RedirectResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel,ConfigDict,Field

from backend.app.core.config import model_settings
from backend.app.services.association import sequential_runner as sequence
from backend.app.services.association import batch_snapshot
from backend.app.core.prediction_limits import PREDICTION_PROMPT_CHARS
from backend.app.models.association_wire import transport_prompt
from backend.app.core.exceptions import BusyError,InputError,ModelServiceError,NotFoundError,ResultNotFound
from backend.app.core.paths import OUTPUTS_DIR,STATIC_DIR
from backend.app.core.runtime import SLOTS
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.association.prediction_review import interrupt_for_review,review_proposal,all_point_proposal
from backend.app.services.measurements.gaussian_batch import gaussian_batch
from backend.app.services.jobs.prediction_execution import PredictionExecution
from backend.app.services.jobs.detached_sse import detached_sse
from backend.app.services.localization.platform_motion import platform_metadata
from backend.app.services.localization.incremental_spatial_plan import build_spatial_plan
from backend.app.services.association.incremental_prediction import (
    apply_answer,create_state,prepare_round,public_state,save_state)
from backend.app.services.association.joint_prediction import create_joint,prepare_joint,apply_joint,review_joint,public_joint
from backend.app.services.inputs.observation_import import import_observations,MAX_BYTES
from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.inputs import workspace_observations
from backend.app.models.live_tracking import IngestLive

router=APIRouter(tags=['incremental prediction'])
ROOT=OUTPUTS_DIR/'incremental'
SOURCE_OUTPUTS=OUTPUTS_DIR
JOINT_ROOT=OUTPUTS_DIR/'joint_prediction'
LOCK=threading.Lock()


def get_prediction_gateway():
    return ConfiguredModelGateway(timeout_override=90,max_prompt_chars=PREDICTION_PROMPT_CHARS,association_transport=True)


class CreatePrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:dict
    seed_ids:list[str]=Field(min_length=1,max_length=5)
    stride:Annotated[int,Field(strict=True,ge=1,le=8)]=1
    horizon:Annotated[float,Field(gt=0,le=600,allow_inf_nan=False)]=20
    max_calls:Annotated[int,Field(strict=True,ge=1,le=100)]=20


class AdvancePrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    consent:bool=False
    question:str=Field(default='',max_length=2000)
    context_digest:str|None=Field(default=None,pattern='^[0-9a-f]{64}$')
    observations:list[dict]|None=Field(default=None,max_length=256)
    selected_times:list[Annotated[float,Field(strict=True,allow_inf_nan=False)]]|None=Field(default=None,min_length=1,max_length=16)


def session_path(session_id):
    if not ResultRepository.valid_id(session_id):raise NotFoundError('任务不存在')
    directory=ROOT/session_id;path=directory/'state.json'
    if ROOT.is_symlink() or directory.is_symlink() or path.is_symlink() or not path.is_file():raise NotFoundError('任务不存在')
    return path


@router.get('/incremental',include_in_schema=False)
def prediction_page():return FileResponse(STATIC_DIR/'incremental.html')


@router.post('/api/incremental/import-observations',summary='解压ZIP或读取JSON观测，不调用模型，不保存上传文件')
async def import_observation_files(files:Annotated[list[UploadFile],File()]):
    try:
        if not 1 <= len(files) <= 16:raise ValueError('每次请导入1至16个文件')
        inputs=[]
        for file in files:
            inputs.append((file.filename or '',await file.read(MAX_BYTES+1)))
        return await asyncio.to_thread(import_observations,inputs)
    except ValueError as exc:
        raise InputError(str(exc)) from None
    finally:
        for file in files:await file.close()


@router.post('/api/incremental/import-platform',summary='读取平台JSON，不调用模型')
async def import_platform_file(file:Annotated[UploadFile,File()]):
    try:
        raw=await file.read(100001)
        if len(raw)>100000:raise ValueError('平台JSON不能超过100KB')
        data=json.loads(raw.decode('utf-8-sig'))
        if isinstance(data,list):data={'platforms':data}
        if isinstance(data,dict) and isinstance(data.get('platform_config'),dict):data=data['platform_config']
        config=platform_context(json.dumps(data,ensure_ascii=False).encode('utf-8'))
        names=[p.get('name') for p in config['platforms']]
        if any(not isinstance(n,str) or not n.strip() or len(n)>80 for n in names) or len(set(names))!=len(names):
            raise ValueError('平台名称须非空且不重复，最多80个字符')
        # Keep valid display colors; they are not navigation/model parameters.
        for platform,source in zip(config['platforms'],data['platforms']):
            color=source.get('color')
            if isinstance(color,str) and len(color)==7 and color.startswith('#') and all(c in '0123456789abcdefABCDEF' for c in color[1:]):platform['color']=color
        return {'platform_config':config,'file_name':file.filename}
    except (UnicodeError,ValueError,TypeError,KeyError,RecursionError):
        raise InputError('平台文件无效：请选择 input/platform.json，包含名称、经纬度、航向、航速与 hull_m / tow_m；不要导入批次配置或目标真值。') from None
    finally:
        await file.close()


@router.get('/api/incremental/batches/{result_id}',summary='导出已保存高斯测量批次，不调用模型')
def get_gaussian_batch(result_id:str):
    if not ResultRepository.valid_id(result_id) or SOURCE_OUTPUTS.is_symlink():raise NotFoundError('结果不存在')
    repo=ResultRepository(SOURCE_OUTPUTS)
    for name in ('inverse_fields.json','measurement_report.json'):
        try:path=repo.file(result_id,name)
        except ResultNotFound:continue
        if path.stat().st_size>16*1024*1024:raise InputError('高斯测量文件超过16MiB限制')
        try:batch=gaussian_batch(json.loads(path.read_text(encoding='utf-8')),result_id,name)
        except (ValueError,TypeError,KeyError,AttributeError):
            raise InputError('高斯测量批次为空、格式不支持或数据无效；未使用像素候选替代') from None
        batch['target_catalog']=target_catalog(repo,result_id,batch)
        platform_config=platform_source_config(result_id)
        if platform_config:batch['platform_config']=platform_config
        return JSONResponse(batch,headers={'Content-Disposition':f'attachment; filename="gaussian-points-{result_id}.json"'})
    raise NotFoundError('该结果尚无高斯测量文件。请先运行原页面预检或识别；platform.json不能作为点位批次')


def platform_source_config(result_id):
    if not isinstance(result_id,str) or not ResultRepository.valid_id(result_id) or SOURCE_OUTPUTS.is_symlink():return None
    try:
        path=ResultRepository(SOURCE_OUTPUTS).file(result_id,'calibration_context.json')
        if path.stat().st_size>100000:return None
        config=platform_metadata(json.loads(path.read_text(encoding='utf-8')))
        if config:config['source_file']='calibration_context.json'
        return config
    except (ResultNotFound,OSError,ValueError,TypeError):return None


def attach_platform_config(state):
    if not state.get('platform_config'):
        config=platform_source_config(state.get('dataset_source',{}).get('source_result_id'))
        if config:state.update(platform_config=config,platform_config_backfilled=True)
    return state


def target_catalog(repo,result_id,batch):
    """Expose measured memberships of saved hypotheses, never infer identity from XY."""
    try:
        ridge_file=repo.file(result_id,'inverse_ridges.json');selection_file=repo.file(result_id,'inverse_selection.json')
    except ResultNotFound:return []
    if any(path.stat().st_size>16*1024*1024 for path in (ridge_file,selection_file)):return []
    try:
        ridges={row['id']:row for row in json.loads(ridge_file.read_text(encoding='utf-8'))['tracks']}
        selected=json.loads(selection_file.read_text(encoding='utf-8'))['selected']
        measurements={point['candidate_id']:point for point in batch['candidates']};catalog=[]
        for row in selected:
            refs=row.get('source_ridges',[])
            if not refs or any(ref not in ridges for ref in refs):continue
            ids=[]
            for ref in refs:
                ridge=ridges[ref]
                for point in ridge['points']:
                    original=measurements.get(point.get('id'))
                    if original and original['array']==ridge['array'] and original['platform_id']==ridge['platform_id'] and original['candidate_id'] not in ids:ids.append(original['candidate_id'])
            scope=[point for point in batch['candidates'] if point['candidate_id'] in ids]
            digest=hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if ids:catalog.append({'target_id':row['id'],'candidate_ids':ids,'source_ridges':refs,'dataset_id':digest,
                'identity_confirmed':False,'association_source':'saved_offline_hypothesis'})
        return catalog
    except (ValueError,TypeError,KeyError,AttributeError):return []


@router.get('/api/incremental/batches/{result_id}/debug-track',summary='免费载入一条离线候选片段，用于单轨逐点调试，非身份真值')
def get_debug_track(result_id:str):
    batch=json.loads(get_gaussian_batch(result_id).body)
    try:path=ResultRepository(SOURCE_OUTPUTS).file(result_id,'inverse_ridges.json')
    except ResultNotFound:raise NotFoundError('该批次没有候选片段文件') from None
    if path.stat().st_size>16*1024*1024:raise InputError('片段文件过大')
    tracks=json.loads(path.read_text(encoding='utf-8')).get('tracks',[])
    tracks=[track for track in tracks if track.get('array')=='hull' and len(track.get('points',[]))>=6]
    if not tracks:raise InputError('没有足够长的hull候选片段可用于调试')
    track=max(tracks,key=lambda item:len(item['points']))
    lookup={p['candidate_id']:p for p in batch['candidates']}
    points=[];times=set()
    for ref in sorted(track['points'],key=lambda p:p['t']):
        point=lookup.get(ref.get('id'))
        if not point or point['array']!='hull' or point['platform_id']!=track.get('platform_id') or point['t'] in times:continue
        points.append(point);times.add(point['t'])
    if len(points)<6:raise InputError('片段与原始测量编号不匹配')
    digest=hashlib.sha256(json.dumps(points,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    batch.update(batch_id=digest,candidates=points,target_catalog=[],point_count=len(points),review_point_count=sum(p['quality']!='fitted' or p['mixed'] for p in points),
        debug_reference={'ridge_id':track['id'],'identity_confirmed':False,'uses_full_run_association':True},
        warnings=['离线关联片段仅用于隔离单轨预测调试，片段选取使用全时段关联，不是在线身份验证，也不是独立真值。'])
    return batch


@router.post('/api/incremental/sessions')
def create_prediction(body:CreatePrediction):
    try:state=create_state(body.dataset,body.seed_ids,body.stride,body.horizon,body.max_calls)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise InputError('候选数据或种子无效，请检查格式、编号、阵列和时间') from exc
    attach_platform_config(state)
    directory=ResultRepository(ROOT).create_run()
    save_state(directory/'state.json',state)
    return {'session_id':directory.name,**public_state(state)}


@router.get('/api/incremental/sessions/{session_id}')
def get_prediction(session_id:str):
    state=json.loads(session_path(session_id).read_text(encoding='utf-8'))
    attach_platform_config(state)
    return {'session_id':session_id,**public_state(state)}


def context_digest(context):
    return hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


@router.get('/api/incremental/sessions/{session_id}/preview',summary='免费准备聊天输入，只返回本轮可用点位')
def preview_prediction(session_id:str,times:str|None=None):
    state=json.loads(session_path(session_id).read_text(encoding='utf-8'))
    attach_platform_config(state)
    try:context,_=prepare_round(state,selected_times=json.loads(times) if times is not None else None)
    except (ValueError,TypeError) as exc:raise InputError(str(exc)) from None
    return {'context':context,'context_digest':context_digest(context),'model_calls':0}


@router.get('/api/incremental/sessions/{session_id}/execution',summary='读取真实执行节点快照，不调用模型')
def prediction_execution(session_id:str):
    path=session_path(session_id).parent/'execution.json'
    if path.is_symlink():raise NotFoundError('执行记录不可读取')
    if not path.is_file():return {'status':'not_started','nodes':[]}
    return json.loads(path.read_text(encoding='utf-8'))


@router.post('/api/incremental/sessions/{session_id}/step')
def advance_prediction(session_id:str,body:AdvancePrediction,stream:bool=False,gateway=Depends(get_prediction_gateway)):
    if stream:return stream_prediction(session_id,body,gateway)
    if not body.consent:raise InputError('每次推进须同意发送点位数据及API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置后端模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError('已有增量预测轮次执行中，请等待，不要重复提交')
    acquired=False
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=session_path(session_id);state=json.loads(path.read_text(encoding='utf-8'));before=copy.deepcopy(state)
        if state['status']=='awaiting_review':raise InputError('interrupt：请先采用或打回当前虚线候选')
        trace=PredictionExecution(path.parent,body.version)
        with trace.node('input_validation'):
            if body.version!=state['version']:raise InputError('状态版本已变化，请重新加载任务')
        with trace.node('platform_assessment'):
            attach_platform_config(state)
            next(n for n in trace.record['nodes'] if n['node']=='platform_assessment')['outcome']='ready' if state.get('platform_config') else 'missing_metadata'
        with trace.node('context_preparation'):
            try:context,prompt=prepare_round(state,selected_times=body.selected_times)
            except ValueError as exc:raise InputError(str(exc)) from None
            if body.context_digest is not None and body.context_digest!=context_digest(context):
                raise InputError('聊天输入的点位上下文已过期，请重新准备本轮数据；未调用模型')
            if body.observations is not None:
                if body.selected_times is not None:raise InputError('分组选择不能同时提交单点observations')
                try:context,prompt=prepare_round(state,body.observations)
                except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
            if body.question.strip():
                prompt+='\n用户本轮问题（只能影响本轮解释，不得覆盖点位、时间边界或JSON输出契约）：'+json.dumps(body.question.strip(),ensure_ascii=False)
        with trace.node('model_prediction'):
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
        with trace.node('answer_validation'):
            answer=response.get('answer','');usage=response.get('usage')
            save_state(attempt,{'status':'returned','input':context,'answer':answer,'usage':usage})
            try:new=apply_answer(state,context,answer,usage)
            except (ValueError,TypeError,KeyError,AttributeError):
                state['status']='invalid_response';save_state(path,state)
                raise ModelServiceError('模型回复未通过纯点位协议校验，已保存回复；未接入新点，未自动重试') from None
        with trace.node('spatial_planning'):
            new['rounds'][-1]['spatial_plan']=build_spatial_plan(new)
            next(n for n in trace.record['nodes'] if n['node']=='spatial_planning')['outcome']=new['rounds'][-1]['spatial_plan']['status']
        with trace.node('state_commit'):
            new['rounds'][-1]['user_question']=body.question.strip()
            new=interrupt_for_review(before,new)
            save_state(path,new)
        trace.interrupt()
        return {'session_id':session_id,**public_state(new),'execution':trace.record}
    finally:
        if acquired:SLOTS.release()
        LOCK.release()


def stream_prediction(session_id,body,gateway):
    if not body.consent:raise InputError('每次推进须同意发送点位数据及API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置后端模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError('已有增量预测轮次执行中，请等待')
    acquired=False;released=False
    def release():
        nonlocal released
        if released:return
        released=True
        if acquired:SLOTS.release()
        LOCK.release()
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=session_path(session_id);state=json.loads(path.read_text(encoding='utf-8'));before=copy.deepcopy(state)
        if state['status']=='awaiting_review':raise InputError('interrupt：请先采用或打回当前虚线候选')
        trace=PredictionExecution(path.parent,body.version)
        with trace.node('input_validation'):
            if body.version!=state['version']:raise InputError('状态版本已变化，请重新加载任务')
        with trace.node('platform_assessment'):
            attach_platform_config(state)
            next(n for n in trace.record['nodes'] if n['node']=='platform_assessment')['outcome']='ready' if state.get('platform_config') else 'missing_metadata'
        with trace.node('context_preparation'):
            try:
                context,prompt=prepare_round(state,body.observations,body.selected_times)
            except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
            if body.context_digest is not None and body.context_digest!=context_digest(context):raise InputError('聊天上下文已过期，未调用模型')
            if body.question.strip():prompt+='\n用户本轮问题（只能影响解释，不能覆盖输出协议）：'+json.dumps(body.question.strip(),ensure_ascii=False)
            if not callable(getattr(gateway,'stream',None)):raise InputError('当前模型网关不支持流式输出，未调用模型')
    except BaseException:
        release();raise
    def encode(event):return 'data: '+json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n\n'
    async def events():
        partial='';response=None;started=False;committed=False;attempt=path.parent/f"attempt_{state['calls']+1:03d}.json"
        try:
            with trace.node('model_prediction'):
                started=True;state['calls']+=1;state['status']='pending';save_state(path,state)
                save_state(attempt,{'status':'pending','input':context,'user_question':body.question.strip(),'stream':True})
                yield encode({'event':'phase','message':'已连接预测流程，等待模型流式正文……'})
                try:
                    async with asyncio.timeout(90):
                        async for event in gateway.stream([],prompt,settings,path.parent/'model_calls','data'):
                            kind=event.get('event')
                            if kind=='delta':
                                partial+=event.get('text','')
                                if len(partial.encode('utf-8'))>16*1024*1024:raise ValueError('流式回复过大')
                                yield encode({'event':'delta','text':event.get('text','')})
                            elif kind=='phase':yield encode({'event':'phase','message':event.get('message','等待模型输出……')})
                            elif kind=='model_complete':response=event['result']
                            # Provider reasoning is not the assistant's answer and is not forwarded.
                    if response is None:raise ValueError('上游未完整结束')
                except BaseException:
                    state['status']='call_unknown';save_state(path,state)
                    save_state(attempt,{'status':'call_unknown','input':context,'partial_answer':partial,'automatic_retry':False,'stream':True})
                    raise
            with trace.node('answer_validation'):
                answer=response.get('answer','');usage=response.get('usage')
                save_state(attempt,{'status':'returned','input':context,'answer':answer,'usage':usage,'user_question':body.question.strip(),'stream':True})
                try:new=apply_answer(state,context,answer,usage)
                except (ValueError,TypeError,KeyError,AttributeError):
                    state['status']='invalid_response';save_state(path,state);raise ValueError('回复校验失败') from None
            yield encode({'event':'phase','phase':'spatial_planning','message':'正在结合平台状态与双阵点位规划目标空间候选……'})
            with trace.node('spatial_planning'):
                new['rounds'][-1]['spatial_plan']=build_spatial_plan(new)
                next(n for n in trace.record['nodes'] if n['node']=='spatial_planning')['outcome']=new['rounds'][-1]['spatial_plan']['status']
            with trace.node('state_commit'):
                new['rounds'][-1]['user_question']=body.question.strip()
                new=interrupt_for_review(before,new);save_state(path,new)
                committed=True
            trace.interrupt()
            yield encode({'event':'result','result':{'session_id':session_id,**public_state(new),'execution':trace.record}})
        except Exception:
            yield encode({'event':'error','message':'模型回复未通过协议校验，已保存原回复；未提交新点，未自动重试。' if state['status']=='invalid_response' else '流式调用失败或中断，未提交新点，未自动重试；可能已计费。'})
        finally:
            try:
                if started and not committed and state['status']=='pending':
                    state['status']='call_unknown';save_state(path,state)
                    save_state(attempt,{'status':'call_unknown','input':context,'partial_answer':partial,'automatic_retry':False,'stream':True})
                if started and not committed and trace.record['status']=='running':
                    trace.record['status']='failed'
                    for node in trace.record['nodes']:
                        if node['status']=='pending':node['status']='skipped'
                    trace.save()
            finally:release()
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no'},background=BackgroundTask(release))


class ReviewPrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    adopt:bool
    reason:str=Field(default='',max_length=2000)


@router.post('/api/incremental/sessions/{session_id}/review')
def review_prediction(session_id:str,body:ReviewPrediction):
    if not LOCK.acquire(blocking=False):raise BusyError('已有任务执行中，请等待')
    try:
        path=session_path(session_id);state=json.loads(path.read_text(encoding='utf-8'))
        try:new=review_proposal(state,body.version,body.adopt,body.reason)
        except ValueError as exc:raise InputError(str(exc)) from None
        save_state(path,new)
        execution_path=path.parent/'execution.json'
        execution=None
        if execution_path.is_file():
            execution=json.loads(execution_path.read_text(encoding='utf-8'))
            execution['status']='completed' if body.adopt else 'rejected'
            execution['sequence']+=1
            for node in execution['nodes']:
                if node['node']=='trajectory_review':node.update(status='completed',outcome='adopted' if body.adopt else 'rejected')
            save_state(execution_path,execution)
            save_state(path.parent/f"execution_{execution['execution_id']}.json",execution)
        return {'session_id':session_id,**public_state(new),**({'execution':execution} if execution else {})}
    finally:LOCK.release()


@router.post('/api/incremental/all-points-preview',summary='免费全量离线试绘，停在人工确认节点')
def preview_all_points(body:CreatePrediction):
    directory=ResultRepository(ROOT).create_run();trace=PredictionExecution(directory,0)
    try:
        with trace.node('input_validation'):
            state=create_state(body.dataset,body.seed_ids,body.stride,body.horizon,body.max_calls)
        with trace.node('platform_assessment'):attach_platform_config(state)
        for node in trace.record['nodes']:
            if node['node'] in ('context_preparation','model_prediction','answer_validation'):
                node.update(status='skipped',outcome='离线试绘不调用模型')
        with trace.node('spatial_planning'):new=all_point_proposal(state)
        with trace.node('state_commit'):save_state(directory/'state.json',new)
        trace.interrupt()
    except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
    return {'session_id':directory.name,**public_state(new),'execution':trace.record}

class JointTrackInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    track_id:str=Field(min_length=1,max_length=80)
    seed_ids:list[str]=Field(min_length=2,max_length=5)


class CreateJointPrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:dict
    tracks:list[JointTrackInput]=Field(min_length=2,max_length=2)
    horizon:Annotated[float,Field(gt=0,le=600,allow_inf_nan=False)]=20
    max_calls:Annotated[int,Field(strict=True,ge=1,le=100)]=20


def joint_path(session_id):
    if not ResultRepository.valid_id(session_id):raise NotFoundError('联合任务不存在')
    directory=JOINT_ROOT/session_id;path=directory/'state.json'
    if JOINT_ROOT.is_symlink() or directory.is_symlink() or path.is_symlink() or not path.is_file():raise NotFoundError('联合任务不存在')
    return path


@router.get('/joint-prediction',include_in_schema=False)
def joint_page():return RedirectResponse('/incremental',status_code=307)


@router.post('/api/incremental/joint-sessions',summary='创建双目标联合任务，免费')
def create_joint_prediction(body:CreateJointPrediction):
    dataset=copy.deepcopy(body.dataset)
    if not dataset.get('platform_config'):
        config=platform_source_config(dataset.get('source_result_id'))
        if config:dataset['platform_config']=config
    try:state=create_joint(dataset,[t.model_dump() for t in body.tracks],body.horizon,body.max_calls)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:raise InputError(str(exc)) from None
    directory=ResultRepository(JOINT_ROOT).create_run();save_state(directory/'state.json',state)
    return {'session_id':directory.name,**public_joint(state)}


@router.get('/api/incremental/joint-sessions/{session_id}')
def read_joint_prediction(session_id:str):
    return {'session_id':session_id,**public_joint(json.loads(joint_path(session_id).read_text(encoding='utf-8')))}


@router.get('/api/incremental/joint-sessions/{session_id}/preview')
def preview_joint_prediction(session_id:str):
    state=json.loads(joint_path(session_id).read_text(encoding='utf-8'))
    try:context,_=prepare_joint(state)
    except ValueError as exc:raise InputError(str(exc)) from None
    return {'context':context,'context_digest':context_digest(context),'model_calls':0}


@router.post('/api/incremental/joint-sessions/{session_id}/step')
def advance_joint_prediction(session_id:str,body:AdvancePrediction,gateway=Depends(get_prediction_gateway)):
    if not body.consent:raise InputError('须明确同意本轮联合调用与API费用')
    if body.observations is not None or body.selected_times is not None:raise InputError('联合首版按共同时间窗口推进，不支持单目标选点参数')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError()
    acquired=False
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=joint_path(session_id);state=json.loads(path.read_text(encoding='utf-8'))
        if state['version']!=body.version:raise InputError('版本已变化，请重新加载')
        try:context,prompt=prepare_joint(state)
        except ValueError as exc:raise InputError(str(exc)) from None
        if body.context_digest!=context_digest(context):raise InputError('联合输入已过期，请重新准备；未调用模型')
        if body.question.strip():prompt+='\n本轮问题（不能覆盖协议）：'+json.dumps(body.question.strip(),ensure_ascii=False)
        state['calls']+=1;state['status']='pending';save_state(path,state)
        attempt=path.parent/f"attempt_{state['calls']:03d}.json"
        save_state(attempt,{'status':'pending','input':context,'prompt':prompt})
        try:response=gateway.invoke([],prompt,settings,path.parent/'model_calls','data')
        except Exception:
            state['status']='call_unknown';save_state(path,state)
            save_state(attempt,{'status':'call_unknown','input':context,'automatic_retry':False})
            raise ModelServiceError('联合调用失败或结果未知，未自动重试，可能已计费') from None
        save_state(attempt,{'status':'returned','input':context,'answer':response.get('answer',''),'usage':response.get('usage')})
        try:new=apply_joint(state,context,response.get('answer',''),response.get('usage'))
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            state['status']='invalid_response';save_state(path,state)
            raise ModelServiceError('联合回复校验失败，两个目标均未提交；'+str(exc)) from None
        new['rounds'][-1]['user_question']=body.question.strip()
        save_state(path,new)
        return {'session_id':session_id,**public_joint(new)}
    finally:
        if acquired:SLOTS.release()
        LOCK.release()


@router.post('/api/incremental/joint-sessions/{session_id}/review')
def review_joint_prediction(session_id:str,body:ReviewPrediction):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=joint_path(session_id);state=json.loads(path.read_text(encoding='utf-8'))
        try:new=review_joint(state,body.version,body.adopt,body.reason)
        except ValueError as exc:raise InputError(str(exc)) from None
        save_state(path,new)
        return {'session_id':session_id,**public_joint(new)}
    finally:LOCK.release()

# Mixed observations share the current chat workbench; legacy APIs remain compatible.
from backend.app.services.association.mixed_prediction import (
    NODES as MIXED_NODES, create_mixed, prepare_mixed, validate_mixed,
    update_mixed, predict_mixed, review_mixed, public_mixed)

MIXED_ROOT=OUTPUTS_DIR/'mixed_prediction'


class CreateMixedPrediction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:dict
    horizon:Annotated[float,Field(gt=0,le=600,allow_inf_nan=False)]=20
    max_calls:Annotated[int,Field(strict=True,ge=1,le=10000)]=20
    persistent_input:bool=False
    timeline_id:str|None=Field(default=None,min_length=1,max_length=120)


class MixedSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    selected_ids:list[str]=Field(min_length=1)
    question:str=Field(default='',max_length=2000)
    recheck:bool=False
    consent:bool=False
    context_digest:str|None=Field(default=None,pattern='^[0-9a-f]{64}$')


def mixed_path(session_id):
    if not ResultRepository.valid_id(session_id):raise NotFoundError('混合观测任务不存在')
    directory=MIXED_ROOT/session_id;path=directory/'state.json'
    if MIXED_ROOT.is_symlink() or directory.is_symlink() or path.is_symlink() or not path.is_file():raise NotFoundError('混合观测任务不存在')
    return path


@router.post('/api/incremental/mixed-sessions',summary='创建混合散点任务，无预设种子或目标数量')
def create_mixed_prediction(body:CreateMixedPrediction):
    dataset=copy.deepcopy(body.dataset)
    if not dataset.get('platform_config'):
        config=platform_source_config(dataset.get('source_result_id'))
        if config:dataset['platform_config']=config
    try:state=create_mixed(dataset,body.horizon,body.max_calls)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:raise InputError(str(exc)) from None
    directory=ResultRepository(MIXED_ROOT).create_run()
    if body.persistent_input:
        state=workspace_observations.enable(directory/'state.json',state,body.timeline_id or 'relative-'+directory.name)
    save_state(directory/'state.json',state)
    return {'session_id':directory.name,**public_mixed(state)}


@router.get('/api/incremental/mixed-sessions/{session_id}')
def read_mixed_prediction(session_id:str):
    return {'session_id':session_id,**public_mixed(read_workspace_state(mixed_path(session_id)))}


def read_workspace_state(path):
    return workspace_observations.hydrate(path,json.loads(path.read_text(encoding='utf-8')))


class EnableWorkspaceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    timeline_id:str=Field(min_length=1,max_length=120)


@router.post('/api/incremental/mixed-sessions/{session_id}/enable-input')
def enable_workspace_input(session_id:str,body:EnableWorkspaceInput):
    if not LOCK.acquire(blocking=False):raise BusyError('原任务正在执行，请结束后再启用；原结果不变')
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['version']!=body.version:raise InputError('版本已变化，请刷新')
        if state['status'] in ('pending','plot_pending'):raise InputError('原任务尚未结束，不能改变其输入机制')
        try:new=workspace_observations.enable(path,state,body.timeline_id)
        except ValueError as exc:raise InputError(str(exc)) from None
        save_state(path,new)
        return {'session_id':session_id,**public_mixed(new),'model_calls':0}
    finally:LOCK.release()


@router.post('/api/incremental/mixed-sessions/{session_id}/observations')
def append_workspace_input(session_id:str,body:IngestLive):
    path=mixed_path(session_id);state=read_workspace_state(path)
    try:receipt=workspace_observations.append(path,state,body.model_dump())
    except ValueError as exc:raise InputError(str(exc)) from None
    origin=state['workspace_input']['origin_ids']
    for row in receipt['results']:
        if row.get('observation_id'):row['candidate_id']=origin.get(row['observation_id'],row['observation_id'])
    return {'receipt':receipt,'model_calls':0}


@router.get('/api/incremental/mixed-sessions/{session_id}/export')
def export_workspace_input(session_id:str):
    path=mixed_path(session_id);state=read_workspace_state(path)
    try:data=workspace_observations.export(path,state)
    except ValueError as exc:raise InputError(str(exc)) from None
    return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="trajectory-workspace.json"'})


class PrepareManualRerun(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    phase:Literal['association','plot']|None=None
    acknowledge_unknown:bool=False


@router.post('/api/incremental/mixed-sessions/{session_id}/prepare-rerun',summary='人工重新准备失败轮次，不调用模型、不重置计费次数')
def prepare_mixed_rerun(session_id:str,body:PrepareManualRerun):
    from backend.app.services.association.manual_rerun import rerun_policy,prepare_manual_rerun
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['version']!=body.version:raise InputError('版本已变化，请刷新后重新准备')
        phase=body.phase or rerun_policy(state)['phase'];source=None
        if phase=='association':
            if state['status'] in ('invalid_response','call_unknown','association_call_unknown'):
                choices=[path.parent/f"attempt_{state['calls']:03d}.json",path.parent/'association_attempt.json']
                attempt=next((p for p in choices if p.is_file() and not p.is_symlink()),None)
                if attempt is None or attempt.stat().st_size>16*1024*1024:raise InputError('没有可恢复的本轮观测快照')
                saved=json.loads(attempt.read_text(encoding='utf-8'))
                if saved.get('status') not in ('returned','call_unknown'):raise InputError('保存的调用仍未结束，不能重新运行')
                source=saved.get('input')
            elif state.get('rounds'):source=state['rounds'][-1]['input']
        try:new,context,prepared=prepare_manual_rerun(state,source,phase=phase,acknowledge_unknown=body.acknowledge_unknown)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:raise InputError(str(exc)) from None
        save_state(path,new)
        return {'session_id':session_id,**public_mixed(new),'rerun':{**prepared,'context':context,'context_digest':context_digest(context)},'model_calls':0}
    finally:LOCK.release()


@router.get('/api/incremental/mixed-sessions/{session_id}/execution')
def mixed_execution(session_id:str):
    path=mixed_path(session_id).parent/'execution.json'
    if path.is_symlink():raise NotFoundError('执行记录不可读取')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status':'not_started','nodes':[]}


@router.post('/api/incremental/mixed-sessions/{session_id}/preview')
def preview_mixed_prediction(session_id:str,body:MixedSelection):
    state=read_workspace_state(mixed_path(session_id))
    if body.version!=state['version']:raise InputError('版本已变化')
    try:context,prompt=prepare_mixed(state,body.selected_ids,body.question,recheck=body.recheck)
    except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
    wire=transport_prompt(prompt)
    return {'context':context,'context_digest':context_digest(context),'model_calls':0,
            'transport_stats':{'observations':len(context['observations']),'canonical_chars':len(prompt),'wire_chars':len(wire),'format':'association-table-v1'}}


@router.post('/api/incremental/mixed-sessions/{session_id}/step')
def advance_mixed_prediction(session_id:str,body:MixedSelection,stream:bool=False,gateway=Depends(get_prediction_gateway)):
    if stream:return stream_mixed_model(session_id,body,gateway,plot=False)
    if not body.consent:raise InputError('须明确同意本轮模型调用及API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置后端模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError()
    acquired=False
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=mixed_path(session_id);state=read_workspace_state(path)
        trace=PredictionExecution(path.parent,body.version,nodes=MIXED_NODES)
        with trace.node('read_points'):
            if body.version!=state['version']:raise InputError('版本已变化，请重新准备')
        with trace.node('normalize'):
            if not set(body.selected_ids)<={p['candidate_id'] for p in state['points']}:raise InputError('观测编号不存在')
        with trace.node('references'):
            try:context,prompt=prepare_mixed(state,body.selected_ids,body.question,recheck=body.recheck)
            except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
            if body.context_digest!=context_digest(context):raise InputError('输入已过期，请重新准备；未调用模型')
        state.pop('rerun_prepared',None)
        state['calls']+=1;state['status']='pending';save_state(path,state)
        attempt=path.parent/f"attempt_{state['calls']:03d}.json"
        save_state(attempt,{'status':'pending','input':context,'prompt':prompt})
        with trace.node('associate'):
            try:response=gateway.invoke([],prompt,settings,path.parent/'model_calls','data')
            except Exception:
                state['status']='call_unknown';save_state(path,state)
                save_state(attempt,{'status':'call_unknown','input':context,'automatic_retry':False})
                raise ModelServiceError('调用失败或结果未知；未重试，可能已计费') from None
        save_state(attempt,{'status':'returned','input':context,'answer':response.get('answer',''),'usage':response.get('usage')})
        with trace.node('validate'):
            try:validated=validate_mixed(state,context,response.get('answer',''))
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                state['status']='invalid_response';save_state(path,state)
                raise ModelServiceError('关联校验失败，未提交任何目标；'+str(exc)) from None
        with trace.node('update'):new=update_mixed(state,context,validated,response.get('usage'))
        trace.defer('predict')
        with trace.node('commit'):save_state(path,new)
        trace.interrupt('确认各目标数据集合后生成初步轨迹')
        return {'session_id':session_id,**public_mixed(new),'execution':trace.record}
    finally:
        if acquired:SLOTS.release()
        LOCK.release()


@router.post('/api/incremental/mixed-sessions/{session_id}/review')
def review_mixed_prediction(session_id:str,body:ReviewPrediction):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['version']!=body.version or state['status']!='awaiting_review':raise InputError('没有待确认集合或版本已变化')
        if not body.adopt and not body.reason.strip():raise InputError('打回须填写原因')
        execution=path.parent/'execution.json'
        trace=PredictionExecution.load(path.parent) if execution.is_file() and not execution.is_symlink() else None
        if trace:
            for node in trace.record['nodes']:
                if node['node']=='trajectory_review':node.update(status='completed',outcome='adopted' if body.adopt else 'rejected')
            trace.record['status']='running';trace.save()
        try:
            new=review_mixed(state,body.version,body.adopt,body.reason);save_state(path,new)
        except ValueError as exc:raise InputError(str(exc)) from None
        if trace:
            trace.record['status']='completed' if body.adopt else 'rejected'
            for node in trace.record['nodes']:
                if node['status'] in ('pending','waiting_for_user'):
                    node['status']='skipped';node['outcome']='requires_separate_model_plot_authorization' if body.adopt else 'rejected'
            trace.save()
        return {'session_id':session_id,**public_mixed(new)}
    finally:LOCK.release()

@router.get('/api/incremental/mixed-sessions/{session_id}/catalog',summary='恢复浏览点库；不作为模型输入')
def mixed_catalog(session_id:str):
    state=read_workspace_state(mixed_path(session_id))
    return {'candidates':state['points'],'platform_config':state['platform_config'],'target_catalog':state['browse_catalog']}

class RecoverMixedAssociation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]


@router.post('/api/incremental/mixed-sessions/{session_id}/recover-association',summary='仅本地复核已返回的保存回复，不调用模型')
def recover_mixed_association(session_id:str,body:RecoverMixedAssociation):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['version']!=body.version or state['status']!='invalid_response':raise InputError('仅能恢复同一版本的已返回但校验失败回复；未知调用不自动恢复')
        attempt=path.parent/f"attempt_{state['calls']:03d}.json"
        if attempt.is_symlink() or not attempt.is_file() or attempt.stat().st_size>16*1024*1024:raise InputError('保存回复不可读取或超过限制')
        try:
            saved=json.loads(attempt.read_text(encoding='utf-8'))
            if saved.get('status')!='returned' or not isinstance(saved.get('input'),dict) or not isinstance(saved.get('answer'),str):raise ValueError('没有已完整返回的模型回复')
        except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
        trace=PredictionExecution(path.parent,body.version,nodes=[
            ('read_saved_reply','读取已保存回复（本地，无模型调用）','local_storage'),
            ('validate','复核最终JSON、引用及数值门限','local_validation'),
            ('update','恢复待确认目标数据集合','local_analysis'),
            ('predict','确认集合后才生成初步轨迹与预测','local_analysis'),
            ('commit','保存恢复结果，等待人工确认','local_storage')])
        trace.record.update(recovered_from_saved_reply=True,model_calls=0);trace.save()
        try:
            with trace.node('read_saved_reply'):
                if saved['input'].get('base_state_version')!=body.version:raise ValueError('保存回复的状态版本不一致')
            with trace.node('validate'):validated=validate_mixed(state,saved['input'],saved['answer'])
            with trace.node('update'):
                new=update_mixed(state,saved['input'],validated,saved.get('usage'))
                new['rounds'][-1]['recovered_from_saved_reply']=True
            trace.defer('predict')
            with trace.node('commit'):save_state(path,new)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:raise InputError('保存回复仍未通过本地校验；未调用模型。'+str(exc)) from None
        trace.interrupt('恢复完成，请确认各目标数据集合')
        return {'session_id':session_id,**public_mixed(new),'execution':trace.record,'model_calls':0}
    finally:LOCK.release()

from backend.app.services.association.model_plot import PLOT_NODES,prepare_model_plot,apply_model_plot

class ModelPlotRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    consent:bool=False
    context_digest:str|None=Field(default=None,pattern='^[0-9a-f]{64}$')
    purpose:Literal['spatial','bearing']='spatial'


class ModelPlotPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    purpose:Literal['spatial','bearing']='spatial'


def check_spatial_dispatch(state,context,purpose,*,explicit_purpose=False):
    if (state.get('workspace_input') or explicit_purpose) and purpose=='spatial' and not context['spatial_references']['candidates']:
        raise InputError('当前空间候选为0：尚无通过校验的跨阵目标。未发送模型、未增加调用；请先补充证据并复核关联，或明确改选方位诊断（不是空间图）。')


def validation_detail(trace,node,exc):
    """Only local validator messages already exposed in the HTTP/SSE error."""
    item=next((n for n in trace.record['nodes'] if n['node']==node),None)
    if item is not None:item['error_detail']=str(exc).replace('\r',' ').replace('\n',' ')[:300]


@router.post('/api/incremental/mixed-sessions/{session_id}/plot-preview',summary='免费准备第二次模型绘图资料')
def preview_model_plot(session_id:str,body:ModelPlotPreview):
    state=read_workspace_state(mixed_path(session_id))
    if state['version']!=body.version:raise InputError('状态版本已变化')
    try:context,_=prepare_model_plot(state,purpose=body.purpose)
    except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
    return {'context':context,'context_digest':context_digest(context),'model_calls':0,
            'can_generate_spatial':bool(context['spatial_references']['candidates']),
            'purpose':body.purpose,'spatial_reason':context['spatial_references']['reason']}


@router.post('/api/incremental/mixed-sessions/{session_id}/plot-step',summary='独立授权第二次模型调用，生成轨迹曲线和预测')
def model_plot_step(session_id:str,body:ModelPlotRequest,stream:bool=False,gateway=Depends(get_prediction_gateway)):
    if stream:return stream_mixed_model(session_id,body,gateway,plot=True)
    if not body.consent:raise InputError('第二次绘图调用须单独同意发送资料和API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError()
    acquired=False
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=mixed_path(session_id);state=read_workspace_state(path)
        trace=PredictionExecution(path.parent,body.version,nodes=PLOT_NODES)
        with trace.node('plot_input'):
            if state['version']!=body.version:raise InputError('绘图版本已变化')
            try:context,prompt=prepare_model_plot(state,purpose=body.purpose)
            except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
            if body.context_digest!=context_digest(context):raise InputError('绘图资料已变化，请免费重新准备；未调用模型')
            check_spatial_dispatch(state,context,body.purpose,explicit_purpose='purpose' in body.model_fields_set)
        state.pop('rerun_prepared',None)
        state['calls']+=1;state['status']='plot_pending';save_state(path,state)
        attempt=path.parent/f"plot_attempt_{state['calls']:03d}.json";save_state(attempt,{'status':'pending','input':context,'prompt':prompt})
        with trace.node('plot_model'):
            try:response=gateway.invoke([],prompt,settings,path.parent/'model_calls','data')
            except Exception:
                state['status']='plot_call_unknown';save_state(path,state)
                save_state(attempt,{'status':'call_unknown','input':context,'automatic_retry':False})
                raise ModelServiceError('第二次绘图调用失败或结果未知；未重试，未使用本地线性替代，可能已计费') from None
        save_state(attempt,{'status':'returned','input':context,'answer':response.get('answer',''),'usage':response.get('usage')})
        with trace.node('plot_validation'):
            try:new=apply_model_plot(state,context,response.get('answer',''),response.get('usage'))
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                validation_detail(trace,'plot_validation',exc)
                state['status']='plot_invalid_response';save_state(path,state)
                raise ModelServiceError('模型绘图回复未通过校验；未线性回退。'+str(exc)) from None
        with trace.node('plot_commit'):commit_model_plot(path,new)
        return {'session_id':session_id,**public_mixed(new),'execution':trace.record}
    finally:
        if acquired:SLOTS.release()
        LOCK.release()


def stream_mixed_model(session_id,body,gateway,*,plot):
    """Real SSE for either paid phase. Provider private reasoning is never relayed."""
    if not body.consent:raise InputError('本次模型调用须单独同意资料发送及API费用')
    settings=model_settings()
    if not settings.api_key:raise InputError('请先配置模型API Key')
    if not LOCK.acquire(blocking=False):raise BusyError()
    acquired=False;released=False
    def release():
        nonlocal released
        if released:return
        released=True
        if acquired:SLOTS.release()
        LOCK.release()
    try:
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:raise BusyError()
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['version']!=body.version:raise InputError('状态版本已变化')
        trace=PredictionExecution(path.parent,body.version,nodes=PLOT_NODES if plot else MIXED_NODES)
        if plot:
            with trace.node('plot_input'):
                try:context,prompt=prepare_model_plot(state,purpose=getattr(body,'purpose','spatial'))
                except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
                if body.context_digest!=context_digest(context):raise InputError('绘图资料已变化')
                check_spatial_dispatch(state,context,getattr(body,'purpose','spatial'),explicit_purpose='purpose' in body.model_fields_set)
        else:
            for node in ('read_points','normalize','references'):
                with trace.node(node):
                    if node=='read_points' and state['version']!=body.version:raise InputError('状态版本已变化')
                    if node=='normalize' and not set(body.selected_ids)<={p['candidate_id'] for p in state['points']}:raise InputError('观测编号不存在')
                    if node=='references':
                        try:context,prompt=prepare_mixed(state,body.selected_ids,body.question,recheck=body.recheck)
                        except (ValueError,TypeError,KeyError) as exc:raise InputError(str(exc)) from None
                        if body.context_digest!=context_digest(context):raise InputError('输入已变化')
    except BaseException:release();raise
    def encode(event):return 'data: '+json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n\n'
    async def events():
        started=False;committed=False;attempt=None;partial=''
        try:
            yield encode({'event':'context','context':context,'phase':'plot' if plot else 'association'})
            state.pop('rerun_prepared',None)
            state['calls']+=1;state['status']='plot_pending' if plot else 'pending';save_state(path,state);started=True
            attempt=path.parent/f"{'plot_attempt' if plot else 'attempt'}_{state['calls']:03d}.json"
            save_state(attempt,{'status':'pending','input':context,'prompt':prompt,'stream':True})
            with trace.node('plot_model' if plot else 'associate'):
                response=None
                if hasattr(gateway,'stream'):
                    async for event in gateway.stream([],prompt,settings,path.parent/'model_calls','data'):
                        if event.get('event')=='delta':
                            partial+=event.get('text','')
                            if len(partial)>4_000_000:raise ValueError('模型正文超出解析限制')
                            yield encode({'event':'delta','text':event.get('text','')})
                        elif event.get('event')=='phase':yield encode({'event':'phase','message':event.get('message','等待模型正文')})
                        elif event.get('event')=='model_complete':response=event['result']
                        # Never expose provider reasoning/thinking to the UI.
                else:
                    yield encode({'event':'phase','message':'当前网关返回整包结果，等待完整正文；不是逐字流式'})
                    response=await asyncio.to_thread(gateway.invoke,[],prompt,settings,path.parent/'model_calls','data')
                if response is None:raise ValueError('模型流未完整结束')
            save_state(attempt,{'status':'returned','input':context,'answer':response.get('answer',''),'usage':response.get('usage'),'stream':True})
            with trace.node('plot_validation' if plot else 'validate'):
                try:
                    if plot:new=apply_model_plot(state,context,response.get('answer',''),response.get('usage'))
                    else:validated=validate_mixed(state,context,response.get('answer',''))
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    validation_detail(trace,'plot_validation' if plot else 'validate',exc)
                    state['status']='plot_invalid_response' if plot else 'invalid_response';save_state(path,state);raise
            if not plot:
                with trace.node('update'):new=update_mixed(state,context,validated,response.get('usage'))
                trace.defer('predict')
            with trace.node('plot_commit' if plot else 'commit'):
                if plot:commit_model_plot(path,new)
                else:save_state(path,new)
                committed=True
            if not plot:trace.interrupt('请在节点核对并确认集合，再授权第二次模型绘图')
            yield encode({'event':'result','result':{'session_id':session_id,**public_mixed(new),'execution':trace.record}})
        except Exception as exc:
            validation=state['status'] in ('invalid_response','plot_invalid_response')
            exhausted=not validation and '上游输出额度或上下文上限已用尽' in str(exc)
            if exhausted:
                item=next(n for n in trace.record['nodes'] if n['node']==('plot_model' if plot else 'associate'))
                item['error_detail']='上游输出额度或上下文上限已用尽，正文被截断，未作为有效JSON结果采用；未自动重试。';trace.save()
            yield encode({'event':'error','code':'reply_validation_failed' if validation else 'model_output_truncated' if exhausted else 'model_stream_failed','message':('模型回复校验失败，原文已保存，未自动重试：'+str(exc)) if validation else '模型输出额度或上下文已用尽，正文被截断；已保留部分正文，未采用残缺结果，可能已计费，未自动重试。' if exhausted else '模型流失败或结果未知；可能已计费，未自动重试，也未使用本地线性替代。'})
        finally:
            try:
                if started and not committed and state['status'] in ('pending','plot_pending'):
                    state['status']='plot_call_unknown' if plot else 'call_unknown';save_state(path,state)
                    save_state(attempt,{'status':'call_unknown','input':context,'partial_answer':partial,'automatic_retry':False,'stream':True})
                if started and not committed and trace.record['status']=='running':
                    trace.record['status']='failed'
                    for node in trace.record['nodes']:
                        if node['status']=='pending':node['status']='skipped'
                    trace.save()
            finally:release()
    observer,release_unstarted=detached_sse(events(),release)
    return StreamingResponse(observer,media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no'},background=BackgroundTask(release_unstarted))


def commit_model_plot(path,state):
    spatial=state['rounds'][-1]['model_plot'].get('spatial_result',{})
    if spatial.get('tracks'):
        from backend.app.services.rendering.trajectory import render_document
        directory=path.parent/f"spatial_v{state['version']:06d}"
        if directory.is_symlink():raise InputError('空间图目录不可写')
        directory.mkdir(exist_ok=True)
        document={'coordinate_system':'local_xy_m','position_observable':True,'plot_kind':'spatial_candidates',
                  'tracks':spatial['tracks'],'warnings':[spatial['reason']]}
        try:
            render_document(document,directory,state.get('platform_config'),filename='estimated_track.png')
            spatial['image_url']=f"/api/incremental/mixed-sessions/{path.parent.name}/spatial-image?revision={state['version']}"
        except Exception as exc:
            spatial['render_error']=type(exc).__name__
            # Valid model coordinates remain available in the interactive graph.
    save_state(path,state)


@router.get('/api/incremental/mixed-sessions/{session_id}/spatial-image')
def model_spatial_image(session_id:str,revision:int):
    path=mixed_path(session_id)
    if not 0<=revision<=1000000:raise InputError('空间图版本无效')
    directory=path.parent/f'spatial_v{revision:06d}';image=directory/'estimated_track.png'
    if directory.is_symlink() or image.is_symlink() or not image.is_file():raise NotFoundError('本版本尚无通过校验的空间轨迹图')
    return FileResponse(image,media_type='image/png',filename=f'model-spatial-{session_id}-v{revision}.png')


@router.post('/api/incremental/mixed-sessions/{session_id}/recover-model-plot')
def recover_model_plot(session_id:str,body:RecoverMixedAssociation):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state['status']!='plot_invalid_response' or state['version']!=body.version:raise InputError('仅能本地复核同版本已返回但校验失败的绘图回复')
        attempt=path.parent/f"plot_attempt_{state['calls']:03d}.json"
        if attempt.is_symlink() or not attempt.is_file() or attempt.stat().st_size>16*1024*1024:raise InputError('没有可复核的完整绘图回复')
        saved=json.loads(attempt.read_text(encoding='utf-8'))
        if saved.get('status')!='returned':raise InputError('调用未知时不能自动恢复')
        trace=PredictionExecution(path.parent,body.version,nodes=[('plot_validation','本地复核已保存模型绘图（不调用模型）','local_validation'),('plot_commit','保存可用结果与空间图缺项说明','local_storage')])
        trace.record.update(recovered_from_saved_reply=True,model_calls=0);trace.save()
        with trace.node('plot_validation'):
            try:new=apply_model_plot(state,saved['input'],saved['answer'],saved.get('usage'))
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                validation_detail(trace,'plot_validation',exc)
                raise InputError('保存绘图回复仍未通过复核：'+str(exc)) from None
            new['rounds'][-1]['model_plot']['recovered_from_saved_reply']=True
        with trace.node('plot_commit'):commit_model_plot(path,new)
        return {'session_id':session_id,**public_mixed(new),'execution':trace.record,'model_calls':0}
    finally:LOCK.release()


# Sequential association stays in the existing mixed workbench namespace.
class SequenceTargetBinding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    entity_type:Literal['physical_target']='physical_target'
    target_id:str=Field(pattern='^T[0-9]{1,12}$')
    revision:Annotated[int,Field(strict=True,ge=0)]

class SequencePrepare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    units:list[list[str]]=Field(min_length=1)
    order:Literal['event_time','received']='event_time'
    call_limit:Annotated[int,Field(strict=True,ge=0,le=10000)]=20
    question:str=Field(default='',max_length=2000)
    target_binding:SequenceTargetBinding|None=None
    revision_window_units:Annotated[int,Field(strict=True,ge=0,le=50)]=3
    review_point_limit:Annotated[int,Field(strict=True,ge=1,le=500)]=80
    long_gap_seconds:Annotated[float,Field(gt=0,le=86400)]=180.
    execution_mode:Literal['sequential','batch-snapshot-v1']='sequential'
    batch_size:Annotated[int,Field(strict=True,ge=1,le=50)]=50
    concurrency:Annotated[int,Field(strict=True,ge=1,le=50)]=50
    retry_limit:Annotated[int,Field(strict=True,ge=0,le=2)]=0
    retry_budget:Annotated[int,Field(strict=True,ge=0,le=1000)]=0
    compact_output:bool=False
    spatial_cards:bool=False

class SequenceCreate(SequencePrepare):
    context_digest:str=Field(pattern='^[a-f0-9]{64}$')
    request_key:str=Field(min_length=1,max_length=100)

class SequenceDispatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_cursor:Annotated[int,Field(strict=True,ge=0)]
    context_digest:str=Field(pattern='^[a-f0-9]{64}$')
    consent:bool=False

class SequenceReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    adopt:bool
    acknowledge_unknown:bool=False

class BatchRetryPrepare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_cursor:Annotated[int,Field(strict=True,ge=0)]
    context_digest:str=Field(pattern='^[a-f0-9]{64}$')
    call_limit:Annotated[int,Field(strict=True,ge=1,le=10000)]
    retry_limit:Annotated[int,Field(strict=True,ge=1,le=2)]=2
    retry_budget:Annotated[int,Field(strict=True,ge=1,le=1000)]=20
    consent:bool=False

class MixedCallBudget(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Annotated[int,Field(strict=True,ge=0)]
    max_calls:Annotated[int,Field(strict=True,ge=1,le=10000)]
    consent:bool=False


@router.post('/api/incremental/mixed-sessions/{session_id}/call-budget')
def update_mixed_call_budget(session_id:str,body:MixedCallBudget):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if not body.consent:raise ValueError('须明确确认调用次数上限调整；不会发送模型')
        if state['version']!=body.version or state.get('active_sequential_run') or state['status']!='ready':raise ValueError('当前会话版本已变或有未结束操作，不能改变已授权队列预算')
        if body.max_calls<state['calls']:raise ValueError('上限不能低于已发生调用数')
        state.setdefault('budget_changes',[]).append({'from':state['max_calls'],'to':body.max_calls,'at_version':state['version']})
        state['max_calls']=body.max_calls;state['version']+=1;save_state(path,state)
        return {'session_id':session_id,**public_mixed(state),'model_calls':0}
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()


def sequence_preview(path,body):
    state=read_workspace_state(path);settings=model_settings()
    if state['version']!=body.version:raise ValueError('会话版本已变化')
    spec=sequence.preview(state,body.units,body.order,body.call_limit,sequence.model_reference(settings),settings.model,body.question,body.target_binding.model_dump() if body.target_binding else None,body.revision_window_units,review_point_limit=body.review_point_limit,long_gap_seconds=body.long_gap_seconds)
    if body.execution_mode==batch_snapshot.MODE and body.target_binding:raise ValueError('快照批次模式使用完整已知轨迹，请取消单目标绑定')
    if body.execution_mode==batch_snapshot.MODE:
        spec.pop('context_digest',None);spec.pop('model_calls',None)
        batch_snapshot.configure(spec,state,body.execution_mode,body.batch_size,body.concurrency,body.retry_limit,body.retry_budget,body.compact_output,body.spatial_cards)
        spec.update(context_digest=sequence.digest(spec),model_calls=0)
    return spec


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/preview')
def preview_sequence(session_id:str,body:SequencePrepare):
    try:return sequence_preview(mixed_path(session_id),body)
    except ValueError as exc:raise InputError(str(exc)) from None


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/prepare-retry')
def prepare_batch_retry(session_id:str,run_id:str,body:BatchRetryPrepare):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        from backend.app.services.association.batch_execution import prepare_retry
        return prepare_retry(mixed_path(session_id),run_id,read_workspace_state,save_state,
            body.expected_cursor,body.context_digest,body.call_limit,body.retry_limit,body.retry_budget,body.consent)
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()


@router.post('/api/incremental/mixed-sessions/{session_id}/runs')
def create_sequence(session_id:str,body:SequenceCreate):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id);state=read_workspace_state(path)
        if state.get('active_sequential_run'):
            existing=sequence.read_run(path,state['active_sequential_run'])
            if existing['request_key']==body.request_key and existing['spec']['context_digest']==body.context_digest:return sequence.public_run(existing)
            raise ValueError('请先结束当前逐组运行')
        spec=sequence_preview(path,body)
        if spec['context_digest']!=body.context_digest:raise ValueError('队列已变化，请重新免费准备')
        run=sequence.create_run(path,state,spec,body.request_key)
        state.update(active_sequential_run=run['id'],status='sequential_prepared',version=run['root_version']);save_state(path,state)
        return sequence.public_run(run)
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()


@router.get('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}')
def get_sequence(session_id:str,run_id:str):
    try:
        path=mixed_path(session_id)
        with sequence.CONTROL:
            run=sequence.read_run(path,run_id)
            if run['status']=='running' and run_id not in sequence.ACTIVE:
                run['status']='result_unknown';run['steps'][-1]['status']='result_unknown';sequence.write_run(path,run)
        # Build geometry/copy multi-MB evidence outside the dispatch ledger lock.
        # read_run already returned an independent versioned snapshot.
        return sequence.public_run(run)
    except ValueError as exc:raise InputError(str(exc)) from None


@router.get('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/snapshots')
def get_batch_snapshots(session_id:str,run_id:str):
    path=mixed_path(session_id);sequence.read_run(path,run_id)
    return {'snapshots':batch_snapshot.snapshots(path,run_id),'model_calls':0}


def check_sequence_dispatch(path,rid,body):
    if not body.consent:raise ValueError('须明确同意逐组模型调用及队列费用上限')
    run=sequence.read_run(path,rid)
    if run['spec']['context_digest']!=body.context_digest or run['cursor']!=body.expected_cursor:raise ValueError('队列授权或步骤已变化，未调用模型')
    if run['status'] not in ('prepared','paused'):raise ValueError('当前运行不可继续，不自动重试失败或未知调用')
    return run


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/next')
def next_sequence(session_id:str,run_id:str,body:SequenceDispatch,gateway=Depends(get_prediction_gateway)):
    return launch_sequence(session_id,run_id,body,gateway,max_steps=1)


def sequence_worker(path,rid,settings,gateway,max_steps=None):
    completed=0
    try:
        while True:
            with sequence.CONTROL:
                run=sequence.read_run(path,rid)
                if run['pause_requested'] or run['status'] not in ('prepared','paused'):break
                cursor=run['cursor']
            acquired=LOCK.acquire(blocking=False);slot=False
            if not acquired:
                with sequence.CONTROL:
                    run=sequence.read_run(path,rid);run['last_error']='后端忙，队列已暂停，未自动重试';sequence.write_run(path,run)
                break
            try:
                slot=SLOTS.acquire(blocking=False)
                if not slot:break
                if run['spec'].get('execution_mode')==batch_snapshot.MODE:
                    batch_snapshot.execute(path,rid,cursor,settings,gateway,read_workspace_state,save_state)
                else:sequence.execute_one(path,rid,cursor,settings,gateway,read_workspace_state,save_state)
                completed+=1
                if max_steps is not None and completed>=max_steps:break
            except Exception as exc:
                # Returned/unknown model status is already durably recorded. Never retry.
                with sequence.CONTROL:
                    current=sequence.read_run(path,rid)
                    if current['status'] in ('prepared','paused'):
                        current['status']='paused';current['last_error']=str(exc)[:500];sequence.write_run(path,current)
                break
            finally:
                if slot:SLOTS.release()
                LOCK.release()
    finally:
        with sequence.CONTROL:sequence.ACTIVE.discard(rid)


def launch_sequence(session_id,run_id,body,gateway,max_steps=None):
    try:
        path=mixed_path(session_id);settings=model_settings()
        with sequence.CONTROL:
            run=check_sequence_dispatch(path,run_id,body)
            if run_id in sequence.ACTIVE:return sequence.public_run(run)
            if run['spec']['model_fingerprint']!=sequence.model_reference(settings):raise ValueError('模型配置已变化')
            if run['spec']['model_call_upper_bound'] and not settings.api_key:raise ValueError('请先配置模型API Key')
            if run['spec'].get('execution_mode')==batch_snapshot.MODE and isinstance(gateway,ConfiguredModelGateway):
                gateway=ConfiguredModelGateway(timeout_override=300,max_prompt_chars=PREDICTION_PROMPT_CHARS)
            run['pause_requested']=False;sequence.write_run(path,run);sequence.ACTIVE.add(run_id)
            threading.Thread(target=sequence_worker,args=(path,run_id,settings,gateway,max_steps),daemon=True).start()
            return sequence.public_run(run)
    except ValueError as exc:raise InputError(str(exc)) from None


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/start')
def start_sequence(session_id:str,run_id:str,body:SequenceDispatch,gateway=Depends(get_prediction_gateway)):
    return launch_sequence(session_id,run_id,body,gateway)


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/pause')
def pause_sequence(session_id:str,run_id:str):
    try:
        with sequence.CONTROL:
            path=mixed_path(session_id);run=sequence.read_run(path,run_id);run['pause_requested']=True
            if run['status']=='prepared':run['status']='paused'
            sequence.write_run(path,run);return sequence.public_run(run)
    except ValueError as exc:raise InputError(str(exc)) from None


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/recover')
def recover_sequence(session_id:str,run_id:str):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id)
        if sequence.read_run(path,run_id)['spec'].get('execution_mode')==batch_snapshot.MODE:return batch_snapshot.recover_saved_proposals(path,run_id,read_workspace_state)
        return sequence.recover_returned(path,run_id,read_workspace_state,save_state)
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/extend-review')
def extend_sequence_review(session_id:str,run_id:str):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:return sequence.extend_review(mixed_path(session_id),run_id,read_workspace_state)
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()


@router.post('/api/incremental/mixed-sessions/{session_id}/runs/{run_id}/review')
def review_sequence(session_id:str,run_id:str,body:SequenceReview):
    if not LOCK.acquire(blocking=False):raise BusyError()
    try:
        path=mixed_path(session_id)
        with sequence.CONTROL:
            if run_id in sequence.ACTIVE:raise ValueError('请先暂停并等待当前步骤完成')
            existing=sequence.read_run(path,run_id)
            if existing['status']=='result_unknown' and not body.acknowledge_unknown:raise ValueError('上次调用结果未知，可能已计费；须确认风险后才能结束此运行')
            state=sequence.review_run(path,run_id,body.adopt,read_workspace_state,save_state)
        return {'session_id':session_id,**public_mixed(state),'model_calls':0}
    except ValueError as exc:raise InputError(str(exc)) from None
    finally:LOCK.release()
