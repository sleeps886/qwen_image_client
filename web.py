"""FastAPI adapter for browser upload and agent integration (local use only)."""
import json
import asyncio
import contextlib
import time
from pathlib import Path
import re
import tempfile
import threading
import uuid
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from client import (ClientError, DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_PROMPT,
                    MAX_FILE_BYTES, MAX_IMAGES, MAX_TOTAL_BYTES,
                    build_request, endpoint, send_images)
from client import UpstreamTimeout, UpstreamConnectionError
from trajectory import platform_context, render_tracks
from config import model_settings, model_timeout,model_request_options
from run_logs import LOGS
from comparison import compare_images
from evaluation import evaluate
from stream_client import stream_images
from data_input import DATA_PROMPT, MAX_DATA_BYTES, prepare_data

ROOT=Path(__file__).resolve().parent
OUTPUTS=ROOT/'outputs'
UPLOADS=ROOT/'work'
SLOTS=threading.BoundedSemaphore(2)


class ImageInfo(BaseModel):
    name:str
    bytes:int
    mime:str
    sha256:str


class RecognitionResponse(BaseModel):
    schema_version:str='1.0'
    ok:bool
    dry_run:bool
    result_id:str|None=None
    answer:str|None=None
    usage:dict|None=None
    downloads:dict[str,str]|None=None
    images:list[ImageInfo]|None=None
    message:str|None=None
    trajectory_image:str|None=None
    trajectory_status:str|None=None
    warnings:list[str]|None=None
    request_id:str|None=None
    input_mode:str|None=None
    input_data:dict|None=None


class UploadLimit:
    """Bound full request before multipart parsing; never log request bodies."""
    def __init__(self, app): self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope.get('method')!='POST':
            return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            raw=message.get('body',b'');size+=len(raw)
            if size>24*1024*1024:
                return await JSONResponse(status_code=413,content={'detail':'请求体超过24MiB限制。'})(scope,receive,send)
            chunks.append(raw)
            if not message.get('more_body',False):break
        sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


app=FastAPI(title='千问图片接入服务',version='1.0.0',description='本地图片上传、预检与百炼调用；网页与智能体共用。')
app.add_middleware(UploadLimit)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])


@app.middleware('http')
async def local_origin(request:Request,call_next):
    origin=request.headers.get('origin')
    if request.method=='POST' and origin:
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or parsed.netloc!=request.headers.get('host'):
            return JSONResponse(status_code=403,content={'detail':'不允许跨站调用本地服务。'})
    rid=None
    is_stream=request.url.path=='/api/recognize/stream'
    if request.method=='POST' and request.url.path in ('/api/recognize','/api/recognize/stream'):
        try:
            rid=LOGS.start(request.headers.get('x-request-id'))
            request.state.request_id=rid
        except ValueError:
            return JSONResponse(status_code=400,content={'detail':'请求编号无效或重复，请重新发起。'})
    try:
        response=await call_next(request)
    except Exception as exc:
        if rid:LOGS.add(rid,'ERROR','服务内部异常：'+type(exc).__name__+'；未输出请求内容。')
        response=JSONResponse(status_code=500,content={'detail':'服务内部异常，请查看运行日志；不要盲目重试云端调用。'})
    if rid:
        if not is_stream or response.status_code>=400:LOGS.finish(rid,response.status_code)
        response.headers['X-Request-ID']=rid
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    if request.url.path not in ('/docs','/redoc','/docs/oauth2-redirect'):
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    # Do not echo submitted field values, which might contain credentials.
    return JSONResponse(status_code=422,content={'detail':'字段格式错误，请检查文件、确认选项及表单。'})


@app.get('/',include_in_schema=False)
def page():return FileResponse(ROOT/'static/index.html')


@app.get('/assets/{filename}',include_in_schema=False)
def asset(filename:str):
    if filename not in ('app.js','style.css'):raise HTTPException(404,'文件不存在')
    return FileResponse(ROOT/'static'/filename)


@app.get('/api/data-example',summary='下载单文件绘图格式示例，不调用模型')
def data_example():
    return FileResponse(ROOT/'examples/target.json',filename='target.json')


@app.get('/api/health')
def health():
    settings=model_settings()
    return {'status':'ok','service':'qwen-image-client','version':'1.0.0',
            'streaming_supported':True,
            'key_configured':bool(settings.api_key),
            'api_format':settings.api_format,
            'request_timeout_s':model_timeout(),
            'provider_label':'DMXAPI（第三方服务）' if urlsplit(settings.base_url).hostname in ('www.dmxapi.cn','dmxapi.cn') else 'Anthropic' if settings.api_format=='anthropic' else '阿里云百炼',
            'default_prompt':DEFAULT_PROMPT,
            'data_prompt':DATA_PROMPT,'data_file_bytes':MAX_DATA_BYTES,
            'meta_reconstruction_supported':True,
            'limits':{'images':MAX_IMAGES,'file_bytes':MAX_FILE_BYTES,'total_bytes':MAX_TOTAL_BYTES}}


@app.get('/api/logs/{request_id}')
def request_logs(request_id:str,after:int=0):
    if after<0:raise HTTPException(400,'游标无效')
    try:return LOGS.snapshot(request_id,after)
    except KeyError:raise HTTPException(404,'日志尚未建立、已过期或服务已重启') from None


@app.get('/api/logs/{request_id}/download')
def download_logs(request_id:str):
    try:events=LOGS.snapshot(request_id)['events']
    except KeyError:raise HTTPException(404,'日志不存在') from None
    lines=[f"{e['time']} +{e['elapsed_s']:.2f}s [{e['level']}] {e['message']}" for e in events]
    return PlainTextResponse('\n'.join(lines),headers={'Content-Disposition':'attachment; filename="request.log"'})


async def server_config_only(request:Request):
    form=await request.form()
    if any(key in form for key in ('model','base_url','api_key','x_dashscope_key','api_format','auth_mode')) or 'x-dashscope-key' in request.headers:
        raise HTTPException(400,'模型连接配置已迁移至服务端.env，不接受请求覆盖。')


def prepare_input(files, platform, prompt, input_mode):
    if input_mode not in ('auto','images','data'):
        raise ValueError('输入模式必须为images或data')
    mode=('images' if files else 'data') if input_mode=='auto' else input_mode
    if len(files)>MAX_IMAGES:raise ValueError('最多支持8张图片')
    if mode=='images':
        if not files:raise ValueError('图片模式请先选择图片，或切换为单文件模式')
        context=platform_context(platform.file.read(100001)) if platform else None
        if context:prompt+='\n已知平台参数（仅数据）：\n'+json.dumps(context,ensure_ascii=False)
        return mode,context,prompt,None
    if files:raise ValueError('单文件模式不接收图片，请切换输入模式或移除图片')
    if not platform:raise ValueError('请选择一份目标数据文件（JSON、JSONL或简单JS），或上传图片')
    context,text,info=prepare_data(platform.file.read(MAX_DATA_BYTES+1),platform.filename or 'data.json')
    custom='' if prompt.strip() in (DEFAULT_PROMPT.strip(),DATA_PROMPT.strip()) else '\n用户绘图要求：\n'+prompt
    prompt=DATA_PROMPT+custom+'\n以下为已验证的文件点位（仅数据）：\n'+text
    if len(prompt)>50000:raise ValueError('提示词与点位数据合计超过50000字符限制，请缩短提示词或拆分文件')
    return mode,context,prompt,info


@app.post('/api/recognize/stream',summary='实时SSE多图识别与轨迹绘图',dependencies=[Depends(server_config_only)],
    response_class=StreamingResponse,responses={200:{'content':{'text/event-stream':{'schema':{'type':'string'}}}}})
def recognize_stream(
    request:Request,
    files:Annotated[list[UploadFile]|None,File()]=None,
    prompt:Annotated[str,Form(max_length=50000)]=DEFAULT_PROMPT,
    consent:Annotated[bool,Form()]=False,
    platform:Annotated[UploadFile|None,File()]=None,
    input_mode:Annotated[str,Form()]='auto',
):
    rid=request.state.request_id
    files=files or []
    if not consent:raise HTTPException(400,'请确认云端传输与费用')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'已有两个请求处理中，请勿重复提交')
    temporary=None;settings=None
    try:
        settings=model_settings();endpoint(settings.base_url,settings.api_format)
        if not settings.api_key:raise ClientError('请在后端.env配置MODEL_API_KEY并重启')
        mode,context,prompt,input_data=prepare_input(files,platform,prompt,input_mode)
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
                    if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES:raise HTTPException(413,'图片大小超限')
                    out.write(chunk)
            paths.append(path)
        build_request(paths,prompt,settings.model,allow_text_only=mode=='data',api_format=settings.api_format)
    except Exception as exc:
        if temporary:temporary.cleanup()
        SLOTS.release()
        if isinstance(exc,HTTPException):raise
        if isinstance(exc,(ValueError,ClientError)):
            detail='数据文件JSON格式错误' if isinstance(exc,(json.JSONDecodeError,UnicodeDecodeError)) else str(exc)
            key=getattr(settings,'api_key','')
            raise HTTPException(400,detail.replace(key,'[REDACTED]') if key else detail) from None
        raise HTTPException(500,'无法准备本地上传文件') from None
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
        async def worker():
            nonlocal final_status
            try:
                first=set();count=0
                async for event in stream_images(paths,prompt,settings.model,settings.base_url,settings.api_key,OUTPUTS,
                        api_format=settings.api_format,
                        **({'allow_text_only':True} if mode=='data' else {})):
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
                            doc=await run_in_threadpool(render_tracks,result['answer'],result['output_dir'],context)
                            trajectory_status='bearing_time' if doc['coordinate_system']=='bearing_time' else 'position_estimate'
                            image=f'/api/results/{result_id}/estimated_track.png'
                            for n in ('estimated_track.png','tracks.json'):downloads[n]=f'/api/results/{result_id}/{n}'
                            warnings+=doc['warnings'];LOGS.add(rid,'INFO','流式模型输出已完成轨迹绘图。')
                        except (ValueError,TypeError,KeyError,ImportError,OSError):
                            warnings.append('模型回复已保存，但没有有效的可绘制轨迹JSON；不会伪造轨迹或自动重试。')
                            LOGS.add(rid,'WARN','模型正文已返回，但轨迹解析/绘图未成功。')
                        await queue.put({'event':'result','data':{'ok':True,'request_id':rid,'result_id':result_id,
                            'answer':result['answer'],'usage':result['usage'],'downloads':downloads,
                            'trajectory_image':image,'trajectory_status':trajectory_status,'warnings':warnings,'input_mode':mode,'input_data':input_data}})
                        continue
                    await queue.put(event)
            except asyncio.CancelledError:raise
            except Exception as exc:
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
            if not task.done():
                final_status=499;LOGS.add(rid,'WARN','浏览器流已断开，停止本地读取；不保证中转站已撤回处理或计费。')
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await task
            cleanup()
    return StreamingResponse(response_stream(),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no','X-Request-ID':rid},background=BackgroundTask(cleanup))


@app.post('/api/recognize',summary='多图识别；dry_run=true时只校验不调用模型',
          response_model=RecognitionResponse,response_model_exclude_none=True,
          dependencies=[Depends(server_config_only)])
def recognize(
    request:Request,
    files: Annotated[list[UploadFile]|None,File(description='图片模式：PNG/JPEG/WebP，最多8张；数据模式无需图片')]=None,
    prompt: Annotated[str,Form(max_length=50000)]=DEFAULT_PROMPT,
    consent: Annotated[bool,Form(description='真实发送需明确同意')]=False,
    dry_run: Annotated[bool,Form()]=False,
    platform: Annotated[UploadFile|None,File(description='图片模式为已知平台参数；数据模式为单份JSON/JSONL/JS目标数据')]=None,
    input_mode: Annotated[str,Form()]='auto',
):
    rid=request.state.request_id
    files=files or []
    log=lambda message,level='INFO':LOGS.add(rid,level,message)
    log('上传已接收，开始校验图片与任务参数。')
    if not dry_run and not consent:raise HTTPException(400,'真实发送前请确认云端传输与费用。')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'已有两个请求处理中，请稍后再试；不要重复提交。')
    try:
        settings=model_settings()
        model,base_url=settings.model,settings.base_url
        endpoint(base_url,settings.api_format)
        log('后端连接配置已加载；密钥状态：'+('已配置' if settings.api_key else '未配置')+'。')
        mode,context,prompt,input_data=prepare_input(files,platform,prompt,input_mode)
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
                            raise HTTPException(413,'单图不得超过10MiB，合计不得超过20MiB。')
                        out.write(chunk)
                paths.append(path)
            _,metadata=build_request(paths,prompt,model,allow_text_only=mode=='data',api_format=settings.api_format)
            log(f'图片校验通过：{len(paths)}张，合计{total/1024/1024:.2f}MiB。')
            if dry_run:
                log('本地预检完成，未调用云端模型。')
                return {'ok':True,'dry_run':True,'request_id':rid,'images':metadata,'input_mode':mode,'input_data':input_data,
                        'message':('数据文件' if mode=='data' else '图片')+'预检通过，未向模型服务发送。'}
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
                result=send_images(paths,prompt,model,base_url,settings.api_key,output_dir=OUTPUTS,timeout=timeout,
                        api_format=settings.api_format,
                        **({'allow_text_only':True} if mode=='data' else {}))
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
                tracks=render_tracks(result['answer'],result['output_dir'],context)
                trajectory_status='bearing_time' if tracks['coordinate_system']=='bearing_time' else 'position_estimate'
                image=f'/api/results/{result_id}/estimated_track.png'
                for name in ('estimated_track.png','tracks.json'):downloads[name]=f'/api/results/{result_id}/{name}'
                warnings=tracks['warnings']
                log('轨迹图已生成，类型：'+('方位—时间（非空间轨迹）' if trajectory_status=='bearing_time' else '模型估计位置轨迹')+'。')
            except (ValueError,TypeError,KeyError,ImportError,OSError) as exc:
                log('未生成轨迹图：'+str(exc),'WARN')
                warnings=['云端回复已保存，但未产生有效、可绘制的轨迹数据。不会伪造轨迹或自动重发。请检查原文；空间轨迹需要已知平台参数及可靠的定位信息。']
            return {'ok':True,'dry_run':False,'result_id':result_id,'answer':result['answer'],
                    'request_id':rid,
                    'usage':result['usage'],'downloads':downloads,'trajectory_image':image,
                    'trajectory_status':trajectory_status,'warnings':warnings,'input_mode':mode,'input_data':input_data}
    except (UpstreamTimeout,UpstreamConnectionError) as exc:
        log(str(exc),'ERROR')
        raise HTTPException(504 if isinstance(exc,UpstreamTimeout) else 502,str(exc)) from None
    except (ClientError,ValueError) as exc:
        message=str(exc)
        key=model_settings().api_key
        if key:message=message.replace(key,'[REDACTED]')
        log(message,'ERROR')
        raise HTTPException(400,message) from None
    except OSError:
        log('本地文件操作失败，请检查权限与磁盘。','ERROR')
        raise HTTPException(500,'本地文件操作失败，请检查权限与磁盘；不要盲目重复发送。') from None
    finally:
        SLOTS.release()
        for upload in files:upload.file.close()
        if platform:platform.file.close()


@app.get('/api/results/{result_id}/{filename}',summary='下载已保存的调用结果')
def result_file(result_id:str,filename:str):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id) or filename not in ('answer.txt','response.json','request_meta.json','estimated_track.png','tracks.json','provider_reasoning.txt','stream_state.json'):
        raise HTTPException(404,'文件不存在')
    path=OUTPUTS/result_id/filename
    if not path.is_file() or path.is_symlink() or (OUTPUTS/result_id).is_symlink():raise HTTPException(404,'文件不存在')
    return FileResponse(path,filename=filename,content_disposition_type='inline' if filename.endswith('.png') else 'attachment')


@app.post('/api/compare',summary='本地比较AI轨迹图与仿真参考图，不调用模型')
def compare(
    result_id:Annotated[str,Form()],
    reference:Annotated[UploadFile,File(description='同一样本truth/btr_A_track.png')],
    reference_kind:Annotated[str,Form(description='参考图类型：position或bearing_time')]='position',
):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id):raise HTTPException(400,'结果编号无效')
    root=OUTPUTS/result_id
    if not root.is_dir() or root.is_symlink():raise HTTPException(404,'AI结果不存在')
    prediction=root/'estimated_track.png'
    tracks=root/'tracks.json'
    if not prediction.is_file() or not tracks.is_file():raise HTTPException(400,'本次没有生成可比较的AI位置轨迹图')
    if prediction.is_symlink() or tracks.is_symlink():raise HTTPException(400,'不接受链接文件')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'服务繁忙，请稍后比较')
    try:
        frame=json.loads(tracks.read_text(encoding='utf-8')).get('coordinate_system')
        if reference_kind not in ('position','bearing_time'):raise HTTPException(400,'参考图类型无效')
        predicted_kind='position' if frame in ('local_xy_m','lon_lat') else 'bearing_time' if frame=='bearing_time' else None
        if not predicted_kind:raise HTTPException(400,'AI轨迹坐标系无效')
        if predicted_kind!=reference_kind:raise HTTPException(400,'两图坐标类型不同：方位—时间图不能与地理位置轨迹图评分')
        raw=reference.file.read(MAX_FILE_BYTES+1)
        if len(raw)>MAX_FILE_BYTES:raise HTTPException(413,'参考图片不能超过10MiB')
        # Unique subdirectory permits repeated comparisons without overwriting evidence.
        cid=uuid.uuid4().hex
        directory=root/'comparisons'/cid;directory.mkdir(parents=True)
        report=compare_images(raw,prediction.read_bytes(),directory)
        report.update(result_id=result_id,comparison_id=cid,reference_kind=reference_kind,predicted_kind=predicted_kind)
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        url=f'/api/comparisons/{result_id}/{cid}'
        return {'ok':True,**report,'overlay_url':url+'/comparison_overlay.png','report_url':url+'/report.json'}
    except (ValueError,OSError):
        raise HTTPException(400,'图像比较失败：请确认参考图为黑底彩色轨迹PNG/JPEG，而不是声呐图；并检查本地写入权限。') from None
    finally:
        reference.file.close();SLOTS.release()


@app.get('/api/history',summary='列出本地已生成的轨迹图，不调用模型')
def history():
    items=[]
    for root in sorted(OUTPUTS.glob('*'),reverse=True):
        if len(items)>=30:break
        if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',root.name) or root.is_symlink():continue
        track=root/'tracks.json';image=root/'estimated_track.png'
        if not track.is_file() or not image.is_file() or track.is_symlink() or image.is_symlink():continue
        try:
            frame=json.loads(track.read_text(encoding='utf-8')).get('coordinate_system')
            items.append({'result_id':root.name,'trajectory_status':'bearing_time' if frame=='bearing_time' else 'position_estimate'})
        except (ValueError,OSError):continue
    return {'items':items}


@app.get('/api/history/{result_id}',summary='载入已生成的本地结果，无需再次调用模型')
def historical_result(result_id:str):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id):raise HTTPException(404,'结果不存在')
    root=OUTPUTS/result_id
    if root.is_symlink():raise HTTPException(404,'结果不存在')
    names=('tracks.json','estimated_track.png','answer.txt')
    if any(not (root/n).is_file() or (root/n).is_symlink() for n in names):raise HTTPException(404,'结果缺少轨迹图、轨迹点或原始正文')
    try:
        doc=json.loads((root/'tracks.json').read_text(encoding='utf-8'))
        return {'ok':True,'result_id':result_id,'answer':(root/'answer.txt').read_text(encoding='utf-8'),
            'input_mode':doc.get('input_mode','images'),
            'input_data':{'plot_kind':doc.get('plot_kind','tracks')},
            'trajectory_status':'bearing_time' if doc['coordinate_system']=='bearing_time' else 'position_estimate',
            'trajectory_image':f'/api/results/{result_id}/estimated_track.png','warnings':doc.get('warnings',[]),
            'downloads':{n:f'/api/results/{result_id}/{n}' for n in names},'usage':None}
    except (OSError,ValueError,KeyError):raise HTTPException(400,'本地结果格式无效') from None


@app.get('/api/comparisons/{result_id}/{comparison_id}/{filename}')
def comparison_file(result_id:str,comparison_id:str,filename:str):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id) or not re.fullmatch(r'[0-9a-f]{32}',comparison_id) or filename not in ('comparison_overlay.png','report.json','evaluation.json'):
        raise HTTPException(404,'文件不存在')
    path=OUTPUTS/result_id/'comparisons'/comparison_id/filename
    if not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in (path.parent,path.parent.parent,path.parent.parent.parent)):
        raise HTTPException(404,'文件不存在')
    return FileResponse(path,filename=filename,content_disposition_type='inline' if filename.endswith('.png') else 'attachment')


@app.post('/api/evaluate',summary='本地结构化评测：目标计数、轨迹匹配、位置误差与时间覆盖率')
def evaluate_result(
    result_id:Annotated[str,Form()],
    semantic:Annotated[UploadFile|None,File(description='同一样本semantic.json，仅本地参考')]=None,
    truth:Annotated[UploadFile|None,File(description='同平台逐时刻btr_A_truth.jsonl')]=None,
    gate_m:Annotated[float,Form(ge=1,le=100000)]=1000,
    max_gap_s:Annotated[float,Form(ge=.1,le=600)]=10,
):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id):raise HTTPException(400,'AI结果编号无效')
    root=OUTPUTS/result_id;path=root/'tracks.json'
    if root.is_symlink() or path.is_symlink() or not path.is_file():raise HTTPException(404,'缺少AI轨迹数据')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'服务繁忙，请稍后评测')
    try:
        semantic_raw=semantic.file.read(2*1024*1024+1) if semantic else None
        truth_raw=truth.file.read(16*1024*1024+1) if truth else None
        if semantic_raw and len(semantic_raw)>2*1024*1024:raise HTTPException(413,'semantic不能超过2MiB')
        if truth_raw and len(truth_raw)>16*1024*1024:raise HTTPException(413,'truth不能超过16MiB')
        report=evaluate(json.loads(path.read_text(encoding='utf-8')),semantic_raw,truth_raw,gate_m,max_gap_s)
        cid=uuid.uuid4().hex;directory=root/'comparisons'/cid;directory.mkdir(parents=True)
        report.update(result_id=result_id,evaluation_id=cid)
        (directory/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        return {'ok':True,**report,'report_url':f'/api/comparisons/{result_id}/{cid}/evaluation.json'}
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        # Messages describe schema problems, never echo the uploaded document.
        detail=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,(json.JSONDecodeError,UnicodeDecodeError)) else '参考文件或AI轨迹格式无效'
        raise HTTPException(400,detail) from None
    except OSError:raise HTTPException(500,'评测文件读写失败，请检查目录权限') from None
    finally:
        SLOTS.release()
        if semantic:semantic.file.close()
        if truth:truth.file.close()
