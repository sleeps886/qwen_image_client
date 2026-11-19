import json
import asyncio
import contextlib
import time
from pathlib import Path
import re
import tempfile
from typing import Annotated
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from backend.app.core.exceptions import ApplicationError,BusyError,InputError,LocalStorageError,ModelServiceError,ModelTimeoutError,PayloadTooLargeError
from backend.app.core.paths import OUTPUTS_DIR as OUTPUTS, UPLOADS_DIR as UPLOADS
from backend.app.core.runtime import SLOTS
from backend.app.core.logging import LOGS
from backend.app.core.config import model_settings,model_timeout,model_request_options
from backend.app.infrastructure.llm.client import ClientError, DEFAULT_PROMPT, MAX_FILE_BYTES, MAX_TOTAL_BYTES, build_request, endpoint, UpstreamTimeout, UpstreamConnectionError
import threading
from backend.app.api.dependencies import server_config_only,prepare_selected_input,get_sonar_agent
from backend.app.schemas.recognition import RecognitionResponse
from backend.app.agents.sonar_agent import SonarAgent
from backend.app.repositories.result_repository import RESULT_FILES,ResultRepository

router=APIRouter(tags=["recognition"])


def add_evidence_downloads(downloads,result_id,directory):
    for name in RESULT_FILES:
        if name in ('candidate_evidence.json','knowledge_sources.json','agent_trace.json','quality_report.json','resolved_observations.json','spatial_raw.json','tracking_report.json','reprojection_report.json','calibration_context.json','tracklets.json','target_count_report.json','execution_report.json','model_observations.json','identity_report.json','measurement_report.json') or name.startswith(('evidence_','selected_','reprojection_','gaussian_')):
            if (Path(directory)/name).is_file():downloads[name]=f'/api/results/{result_id}/{name}'


@router.post('/api/recognize/stream',summary='实时SSE多图识别与轨迹绘图',dependencies=[Depends(server_config_only)],
    response_class=StreamingResponse,responses={200:{'content':{'text/event-stream':{'schema':{'type':'string'}}}}})
def recognize_stream(
    request:Request,
    workflow:Annotated[SonarAgent,Depends(get_sonar_agent)],
    files:Annotated[list[UploadFile]|None,File()]=None,
    prompt:Annotated[str,Form(max_length=50000)]=DEFAULT_PROMPT,
    consent:Annotated[bool,Form()]=False,
    platform:Annotated[UploadFile|None,File()]=None,
    input_mode:Annotated[str,Form()]='auto',
    renderer_profile:Annotated[str,Form()]='none',
):
    rid=request.state.request_id
    files=files or []
    if not consent:raise InputError('请确认云端传输与费用')
    if not SLOTS.acquire(blocking=False):raise BusyError('已有两个请求处理中，请勿重复提交')
    temporary=None;settings=None
    try:
        settings=model_settings();endpoint(settings.base_url,settings.api_format)
        if not settings.api_key:raise ClientError('请在后端.env配置MODEL_API_KEY并重启')
        mode,context,prompt,input_data=prepare_selected_input(files,platform,prompt,input_mode,renderer_profile)
        UPLOADS.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix='stream_',dir=UPLOADS)
        paths=[];total=0
        for i,upload in enumerate(files):
            name=(upload.filename or 'image').replace('\\','/').split('/')[-1]
            name=re.sub(r'[^\w.\-]','_',name)[:100] or 'image'
            path=Path(temporary.name)/f'{i:02d}_{name}';size=0
            with path.open('wb') as out:
                while chunk:=upload.file.read(256*1024):
                    size+=len(chunk);total+=len(chunk)
                    if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES:raise PayloadTooLargeError('图片大小超限')
                    out.write(chunk)
            paths.append(path)
        build_request(paths,prompt,settings.model,allow_text_only=mode=='data',api_format=settings.api_format)
    except Exception as exc:
        if temporary:temporary.cleanup()
        SLOTS.release()
        if isinstance(exc,ApplicationError) and not isinstance(exc,ClientError):raise
        if isinstance(exc,(ValueError,ClientError)):
            detail='数据文件JSON格式错误' if isinstance(exc,(json.JSONDecodeError,UnicodeDecodeError)) else str(exc)
            key=getattr(settings,'api_key','')
            raise InputError(detail.replace(key,'[REDACTED]') if key else detail) from None
        raise LocalStorageError('无法准备本地上传文件') from None
    finally:
        for upload in files:upload.file.close()
        if platform:platform.file.close()
    LOGS.add(rid,'INFO',f'流式请求校验通过，图片{len(paths)}张；stream=true，本地不设等待时限。')
    LOGS.add(rid,'INFO',f'后端接口格式：{settings.api_format}；模型：{settings.model}。')
    if model_request_options(settings.api_format).get('reasoning_effort')=='low':
        LOGS.add(rid,'INFO','本次请求设置reasoning_effort=low；中转站是否遵守不能由客户端保证。')
    cleaned=False;final_status=200
    def cleanup():
        nonlocal cleaned
        if cleaned:return
        cleaned=True
        try:temporary.cleanup()
        finally:
            SLOTS.release();LOGS.finish(rid,final_status)
    def encode(event):
        return 'event: '+event['event']+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
    async def response_stream():
        nonlocal final_status
        queue=asyncio.Queue(maxsize=32);started=time.monotonic()
        loop=asyncio.get_running_loop();pending_nodes=[];stream_alive=True
        def forward_node(event):
            if not stream_alive or loop.is_closed():return
            pending_nodes.append(asyncio.run_coroutine_threadsafe(queue.put(event),loop))
        workflow.node_callback=forward_node
        async def drain_nodes():
            if pending_nodes:
                batch=list(pending_nodes);pending_nodes.clear()
                await asyncio.gather(*(asyncio.wrap_future(f) for f in batch))
        async def worker():
            nonlocal final_status
            try:
                first=set();count=0
                async for event in workflow.stream(paths,prompt,settings,OUTPUTS,mode,context=context):
                    kind=event['event']
                    if kind=='phase':LOGS.add(rid,'INFO',event['message'])
                    elif kind in ('delta','reasoning'):
                        if kind not in first:
                            first.add(kind);LOGS.add(rid,'INFO','收到首个'+('正文' if kind=='delta' else '模型服务推理文本')+'片段。')
                        count+=1
                    elif kind=='model_complete':
                        LOGS.add(rid,'INFO',f'上游输出结束，累计{count}个文本片段；开始轨迹解析。')
                        result=event['result'];result_id=Path(result['output_dir']).name
                        downloads={n:f'/api/results/{result_id}/{n}' for n in ('answer.txt','response.json','request_meta.json','stream_state.json')}
                        if result['has_reasoning']:downloads['provider_reasoning.txt']=f'/api/results/{result_id}/provider_reasoning.txt'
                        await queue.put({'event':'phase','phase':'rendering','message':'模型正文已接收完毕，正在校验轨迹JSON并绘图。'})
                        image=None;trajectory_status='not_generated';warnings=[]
                        if result['finish_reason'] not in (None,'stop','buffered_response'):
                            warnings.append('模型结束原因：'+result['finish_reason']+'；输出可能不完整。')
                        try:
                            doc=await run_in_threadpool(workflow.render,result['answer'],result['output_dir'],context)
                            trajectory_status='bearing_time' if doc['coordinate_system']=='bearing_time' else 'position_estimate'
                            image=f'/api/results/{result_id}/estimated_track.png'
                            for n in ('estimated_track.png','tracks.json'):downloads[n]=f'/api/results/{result_id}/{n}'
                            warnings+=doc['warnings'];LOGS.add(rid,'INFO','流式模型输出已完成轨迹绘图。')
                        except (ValueError,TypeError,KeyError,ImportError,OSError) as exc:
                            warnings.append('未生成轨迹图：'+str(exc).replace(settings.api_key,'[REDACTED]')[:500])
                            LOGS.add(rid,'WARN','模型正文已返回，但轨迹解析/绘图未成功。')
                        await drain_nodes()
                        add_evidence_downloads(downloads,result_id,result['output_dir'])
                        await queue.put({'event':'result','data':{'ok':True,'request_id':rid,'result_id':result_id,
                            'answer':result['answer'],'usage':result['usage'],'downloads':downloads,
                            'trajectory_image':image,'trajectory_status':trajectory_status,'warnings':warnings,'input_mode':mode,'input_data':input_data,
                            **getattr(workflow,'result_metadata',lambda:{})()}})
                        continue
                    await queue.put(event)
            except asyncio.CancelledError:raise
            except Exception as exc:
                await drain_nodes()
                final_status=502
                message=str(exc) if isinstance(exc,ClientError) else '流式处理失败：'+type(exc).__name__+'；未自动重试。'
                message=message.replace(settings.api_key,'[REDACTED]')
                LOGS.add(rid,'ERROR',message)
                await queue.put({'event':'error','message':message,'partial':True,'request_id':rid})
            await queue.put(None)
        task=asyncio.create_task(worker())
        try:
            yield encode({'event':'phase','phase':'accepted','message':'本地已验证'+('目标数据文件' if mode=='data' else '图片')+'，开始真正的上游流式请求。','request_id':rid})
            while True:
                try:event=await asyncio.wait_for(queue.get(),timeout=15)
                except asyncio.TimeoutError:
                    event={'event':'heartbeat','elapsed_s':round(time.monotonic()-started),
                           'message':'连接仍在等待或处理中；心跳不代表模型已生成内容。'}
                if event is None:break
                yield encode(event)
        finally:
            stream_alive=False;workflow.node_callback=None
            for future in pending_nodes:future.cancel()
            if not task.done():
                final_status=499;LOGS.add(rid,'WARN','浏览器流已断开，停止本地读取；不保证中转站已撤回处理或计费。')
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await task
            cleanup()
    return StreamingResponse(response_stream(),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no','X-Request-ID':rid},background=BackgroundTask(cleanup))


@router.post('/api/recognize',summary='多图识别；dry_run=true时只校验不调用模型',
          response_model=RecognitionResponse,response_model_exclude_none=True,
          dependencies=[Depends(server_config_only)])
def recognize(
    request:Request,
    workflow:Annotated[SonarAgent,Depends(get_sonar_agent)],
    files: Annotated[list[UploadFile]|None,File(description='图片模式：PNG/JPEG/WebP，最多8张；数据模式无需图片')]=None,
    prompt: Annotated[str,Form(max_length=50000)]=DEFAULT_PROMPT,
    consent: Annotated[bool,Form(description='真实发送需明确同意')]=False,
    dry_run: Annotated[bool,Form()]=False,
    platform: Annotated[UploadFile|None,File(description='图片模式为已知平台参数；数据模式为单份JSON/JSONL/JS目标数据')]=None,
    input_mode: Annotated[str,Form()]='auto',
    renderer_profile:Annotated[str,Form()]='none',
):
    rid=request.state.request_id
    files=files or []
    log=lambda message,level='INFO':LOGS.add(rid,level,message)
    log('上传已接收，开始校验图片与任务参数。')
    if not dry_run and not consent:raise InputError('真实发送前请确认云端传输与费用。')
    if not SLOTS.acquire(blocking=False):raise BusyError('已有两个请求处理中，请稍后再试；不要重复提交。')
    try:
        settings=model_settings()
        model,base_url=settings.model,settings.base_url
        endpoint(base_url,settings.api_format)
        log('后端连接配置已加载；密钥状态：'+('已配置' if settings.api_key else '未配置')+'。')
        mode,context,prompt,input_data=prepare_selected_input(files,platform,prompt,input_mode,renderer_profile)
        if platform:log('已知平台JSON校验通过。' if mode=='images' else '单文件点位数据校验通过；不是声呐反演。')
        UPLOADS.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='upload_',dir=UPLOADS) as temp:
            paths=[];total=0
            for i,upload in enumerate(files):
                name=(upload.filename or f'image_{i+1}').replace('\\','/').split('/')[-1]
                name=re.sub(r'[^\w.\-]','_',name)[:100] or 'image'
                path=Path(temp)/f'{i+1:02d}_{name}'
                size=0
                with path.open('wb') as out:
                    while chunk:=upload.file.read(256*1024):
                        size+=len(chunk);total+=len(chunk)
                        if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES:
                            raise PayloadTooLargeError('单图不得超过10MiB，合计不得超过20MiB。')
                        out.write(chunk)
                paths.append(path)
            _,metadata=build_request(paths,prompt,model,allow_text_only=mode=='data',api_format=settings.api_format)
            log(f'图片校验通过：{len(paths)}张，合计{total/1024/1024:.2f}MiB。')
            if dry_run:
                extra={}
                if mode=='images' and context and context.get('platforms'):
                    workflow.prepare(paths,prompt,mode,context)
                    directory=ResultRepository(OUTPUTS).create_run()
                    workflow.persist_evidence(directory)
                    downloads={};add_evidence_downloads(downloads,directory.name,directory)
                    extra={'result_id':directory.name,'downloads':downloads,
                           'warnings':['仅本地候选与绘图区预检，尚未运行模型关联或空间定位。']}
                log('本地预检完成，未调用云端模型。')
                return {'ok':True,'dry_run':True,'request_id':rid,'images':metadata,'input_mode':mode,'input_data':input_data,
                        'message':('数据文件' if mode=='data' else '图片')+'预检通过，未向模型服务发送。',**extra,
                        **getattr(workflow,'result_metadata',lambda:{})()}
            if not settings.api_key:raise ClientError('请在服务端.env中填写MODEL_API_KEY，然后重启服务。')
            timeout=model_timeout()
            log('开始调用模型服务，等待响应（本地不设等待时限；不自动重试）。')
            finished=threading.Event()
            def waiting_log():
                waited=0
                while not finished.wait(15):
                    waited+=15
                    log(f'已等待模型服务约{waited}秒，尚未收到完整响应；这不是模型生成进度。')
            heartbeat=threading.Thread(target=waiting_log,daemon=True);heartbeat.start()
            try:
                result=workflow.invoke(paths,prompt,settings,OUTPUTS,mode,context=context)
            finally:
                finished.set();heartbeat.join(.2)
            log('模型已返回，回复原文已保存。')
            result_id=Path(result['output_dir']).name
            downloads={name:f'/api/results/{result_id}/{name}' for name in ('answer.txt','response.json','request_meta.json')}
            image=None
            trajectory_status='not_generated'
            warnings=[]
            try:
                log('开始校验模型轨迹JSON并绘制图片。')
                tracks=workflow.render(result['answer'],result['output_dir'],context)
                trajectory_status='bearing_time' if tracks['coordinate_system']=='bearing_time' else 'position_estimate'
                image=f'/api/results/{result_id}/estimated_track.png'
                for name in ('estimated_track.png','tracks.json'):downloads[name]=f'/api/results/{result_id}/{name}'
                warnings=tracks['warnings']
                log('轨迹图已生成，类型：'+('方位—时间（非空间轨迹）' if trajectory_status=='bearing_time' else '模型估计位置轨迹')+'。')
            except (ValueError,TypeError,KeyError,ImportError,OSError) as exc:
                log('未生成轨迹图：'+str(exc),'WARN')
                warnings=['未生成轨迹图：'+str(exc).replace(settings.api_key,'[REDACTED]')[:500]]
            add_evidence_downloads(downloads,result_id,result['output_dir'])
            return {'ok':True,'dry_run':False,'result_id':result_id,'answer':result['answer'],
                    'request_id':rid,
                    'usage':result['usage'],'downloads':downloads,'trajectory_image':image,
                    'trajectory_status':trajectory_status,'warnings':warnings,'input_mode':mode,'input_data':input_data,
                    **getattr(workflow,'result_metadata',lambda:{})()}
    except (UpstreamTimeout,UpstreamConnectionError) as exc:
        log(str(exc),'ERROR')
        raise (ModelTimeoutError if isinstance(exc,UpstreamTimeout) else ModelServiceError)(str(exc)) from None
    except (ClientError,ValueError) as exc:
        message=str(exc)
        key=model_settings().api_key
        if key:message=message.replace(key,'[REDACTED]')
        log(message,'ERROR')
        raise InputError(message) from None
    except OSError:
        log('本地文件操作失败，请检查权限与磁盘。','ERROR')
        raise LocalStorageError('本地文件操作失败，请检查权限与磁盘；不要盲目重复发送。') from None
    finally:
        SLOTS.release()
        for upload in files:upload.file.close()
        if platform:platform.file.close()


