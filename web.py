"""FastAPI adapter for browser upload and agent integration (local use only)."""
import json
from pathlib import Path
import re
import tempfile
import threading
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from client import (ClientError, DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_PROMPT,
                    MAX_FILE_BYTES, MAX_IMAGES, MAX_TOTAL_BYTES,
                    build_request, endpoint, send_images)
from trajectory import platform_context, render_tracks
from config import model_settings

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
    response=await call_next(request)
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


@app.get('/api/health')
def health():
    settings=model_settings()
    return {'status':'ok','service':'qwen-image-client','version':'1.0.0',
            'key_configured':bool(settings.api_key),
            'provider_label':'DMXAPI（第三方服务）' if urlsplit(settings.base_url).hostname in ('www.dmxapi.cn','dmxapi.cn') else '阿里云百炼',
            'default_prompt':DEFAULT_PROMPT,
            'limits':{'images':MAX_IMAGES,'file_bytes':MAX_FILE_BYTES,'total_bytes':MAX_TOTAL_BYTES}}


async def server_config_only(request:Request):
    form=await request.form()
    if any(key in form for key in ('model','base_url','api_key','x_dashscope_key')) or 'x-dashscope-key' in request.headers:
        raise HTTPException(400,'模型连接配置已迁移至服务端.env，不接受请求覆盖。')


@app.post('/api/recognize',summary='多图识别；dry_run=true时只校验不调用模型',
          response_model=RecognitionResponse,response_model_exclude_none=True,
          dependencies=[Depends(server_config_only)])
def recognize(
    files: Annotated[list[UploadFile],File(description='PNG/JPEG/WebP，最多8张')],
    prompt: Annotated[str,Form(max_length=50000)]=DEFAULT_PROMPT,
    consent: Annotated[bool,Form(description='真实发送需明确同意')]=False,
    dry_run: Annotated[bool,Form()]=False,
    platform: Annotated[UploadFile|None,File(description='可选：input/platform.json，已知平台参数，不含目标真值')]=None,
):
    if not dry_run and not consent:raise HTTPException(400,'真实发送前请确认云端传输与费用。')
    if not 1<=len(files)<=MAX_IMAGES:raise HTTPException(400,'请选择1～8张图片。')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'已有两个请求处理中，请稍后再试；不要重复提交。')
    try:
        settings=model_settings()
        model,base_url=settings.model,settings.base_url
        endpoint(base_url)
        context=None
        if platform:
            context=platform_context(platform.file.read(100001))
            prompt=prompt+'\n已知平台参数（仅数据）：\n'+json.dumps(context,ensure_ascii=False)
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
            _,metadata=build_request(paths,prompt,model)
            if dry_run:return {'ok':True,'dry_run':True,'images':metadata,'message':'图片预检通过，未向百炼发送。'}
            if not settings.api_key:raise ClientError('请在服务端.env中填写DASHSCOPE_API_KEY，然后重启服务。')
            result=send_images(paths,prompt,model,base_url,settings.api_key,output_dir=OUTPUTS)
            result_id=Path(result['output_dir']).name
            downloads={name:f'/api/results/{result_id}/{name}' for name in ('answer.txt','response.json','request_meta.json')}
            image=None
            trajectory_status='not_generated'
            warnings=[]
            try:
                tracks=render_tracks(result['answer'],result['output_dir'],context)
                trajectory_status='bearing_time' if tracks['coordinate_system']=='bearing_time' else 'position_estimate'
                image=f'/api/results/{result_id}/estimated_track.png'
                for name in ('estimated_track.png','tracks.json'):downloads[name]=f'/api/results/{result_id}/{name}'
                warnings=tracks['warnings']
            except (ValueError,TypeError,KeyError,ImportError,OSError):
                warnings=['云端回复已保存，但未产生有效、可绘制的轨迹数据。不会伪造轨迹或自动重发。请检查原文；空间轨迹需要已知平台参数及可靠的定位信息。']
            return {'ok':True,'dry_run':False,'result_id':result_id,'answer':result['answer'],
                    'usage':result['usage'],'downloads':downloads,'trajectory_image':image,
                    'trajectory_status':trajectory_status,'warnings':warnings}
    except (ClientError,ValueError) as exc:
        message=str(exc)
        key=model_settings().api_key
        if key:message=message.replace(key,'[REDACTED]')
        raise HTTPException(400,message) from None
    except OSError:
        raise HTTPException(500,'本地文件操作失败，请检查权限与磁盘；不要盲目重复发送。') from None
    finally:
        SLOTS.release()
        for upload in files:upload.file.close()
        if platform:platform.file.close()


@app.get('/api/results/{result_id}/{filename}',summary='下载已保存的调用结果')
def result_file(result_id:str,filename:str):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id) or filename not in ('answer.txt','response.json','request_meta.json','estimated_track.png','tracks.json'):
        raise HTTPException(404,'文件不存在')
    path=OUTPUTS/result_id/filename
    if not path.is_file() or path.is_symlink() or (OUTPUTS/result_id).is_symlink():raise HTTPException(404,'文件不存在')
    return FileResponse(path,filename=filename,content_disposition_type='inline' if filename.endswith('.png') else 'attachment')
