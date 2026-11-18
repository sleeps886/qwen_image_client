"""Local inverse endpoints. Cloud access is an independent explicit opt-in."""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Annotated

from fastapi import APIRouter,Depends,File,Form,HTTPException,Request,UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from starlette.background import BackgroundTask

from backend.app.core.paths import OUTPUTS_DIR as OUTPUTS,UPLOADS_DIR as UPLOADS
from backend.app.core.constants import MAX_FILE_BYTES,MAX_TOTAL_BYTES
from backend.app.core.runtime import SLOTS
from backend.app.core.logging import LOGS
from backend.app.core.config import model_settings
from backend.app.infrastructure.llm.client import endpoint
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.input_service import prepare_input
from backend.app.services.inversion_control import InversionControl
from backend.app.workflows.forward_inverse import ForwardInverseWorkflow
from backend.app.api.dependencies import server_config_only

router=APIRouter(tags=['forward-inversion'],dependencies=[Depends(server_config_only)])


@dataclass
class InverseJob:
    temporary:object
    paths:list
    context:dict
    control:InversionControl
    reviewer:object=None
    closed:bool=False

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.temporary.cleanup()
        finally:SLOTS.release()


def prepare_job(files,platform,renderer_profile,review_model,consent):
    if review_model and not consent:raise HTTPException(400,'申请模型复核必须明确同意云端传输与费用；本地反演不需要勾选')
    if len(files)!=2:raise HTTPException(400,'正向反演需要hull/tow各一张图')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'本地计算已有两个任务，请勿重复提交')
    temporary=None
    try:
        raw=platform.file.read(100001)
        prepared=prepare_input(2,raw,'','images',renderer_profile=renderer_profile)
        if renderer_profile!='btr_gui_v1':raise ValueError('请选择本工具原始BTR渲染配置；不对未知配色自动反演')
        if len(prepared.context['platforms'])!=1:raise ValueError('首期完整正向反演只支持一个平台')
        UPLOADS.mkdir(parents=True,exist_ok=True);temporary=tempfile.TemporaryDirectory(prefix='inverse_',dir=UPLOADS)
        paths=[];total=0
        for i,upload in enumerate(files):
            name=(upload.filename or 'image').replace('\\','/').split('/')[-1]
            name=re.sub(r'[^\w.\-]','_',name)[:100] or 'image'
            path=Path(temporary.name)/f'{i:02d}_{name}';size=0
            with path.open('wb') as out:
                while chunk:=upload.file.read(256*1024):
                    size+=len(chunk);total+=len(chunk)
                    if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES:raise HTTPException(413,'单图不得超过10MiB，合计不得超过20MiB')
                    out.write(chunk)
            with Image.open(path) as image:
                if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>16000000:
                    raise ValueError('仅支持PNG/JPEG/WebP且每图最多1600万像素')
            paths.append(path)
        reviewer=None
        if review_model:
            settings=model_settings();endpoint(settings.base_url,settings.api_format)
            if not settings.api_key:raise ValueError('模型复核需要服务端API Key；可取消复核仅运行本地反演')
            gateway=ConfiguredModelGateway(timeout_override=45)
            def reviewer(images,prompt,directory):
                return gateway.invoke(images,prompt,settings,directory/'review_provider','images')
        return InverseJob(temporary,paths,prepared.context,InversionControl(),reviewer)
    except Exception as exc:
        if temporary:temporary.cleanup()
        SLOTS.release()
        if isinstance(exc,HTTPException):raise
        raise HTTPException(400,str(exc)[:250] if isinstance(exc,(ValueError,OSError)) else '无法准备本地反演输入') from None
    finally:
        for upload in files:upload.file.close()
        platform.file.close()


@router.post('/api/invert',summary='完整正向反演；默认只本地计算，模型复核需单独授权')
def invert(request:Request,files:Annotated[list[UploadFile],File()],platform:Annotated[UploadFile,File()],
           renderer_profile:Annotated[str,Form()]='none',review_model:Annotated[bool,Form()]=False,
           consent:Annotated[bool,Form()]=False):
    job=prepare_job(files,platform,renderer_profile,review_model,consent)
    workflow=ForwardInverseWorkflow(control=job.control,reviewer=job.reviewer)
    try:return workflow.run(job.paths,job.context,OUTPUTS)|{'request_id':request.state.request_id}
    except ValueError as exc:raise HTTPException(422,{'message':str(exc)[:250],
        'result_id':workflow.directory.name if workflow.directory else None,'automatic_retry':False}) from None
    finally:job.close()


@router.post('/api/invert/stream',summary='完整正向反演SSE：实际节点、可取消、默认零模型调用')
def invert_stream(request:Request,files:Annotated[list[UploadFile],File()],platform:Annotated[UploadFile,File()],
                  renderer_profile:Annotated[str,Form()]='none',review_model:Annotated[bool,Form()]=False,
                  consent:Annotated[bool,Form()]=False):
    job=prepare_job(files,platform,renderer_profile,review_model,consent);rid=request.state.request_id
    def encode(event):return 'event: '+event['event']+'\ndata: '+json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n\n'
    task=None;final_status=200;cleanup_scheduled=False
    def finalize():
        nonlocal cleanup_scheduled
        # Cancelling an SSE response does not stop its numerical worker thread.
        # Keep uploads and the concurrency slot until that worker really exits.
        if task is not None and not task.done():
            job.control.cancelled.set()
            if not cleanup_scheduled:
                cleanup_scheduled=True
                task.add_done_callback(lambda _:finalize())
            return
        if not job.closed:
            job.close();LOGS.finish(rid,final_status)
    async def background_cleanup():finalize()
    async def events():
        nonlocal task,final_status
        queue=asyncio.Queue();loop=asyncio.get_running_loop();alive=True
        def emit(event):
            if alive and not loop.is_closed():loop.call_soon_threadsafe(queue.put_nowait,event)
        workflow=ForwardInverseWorkflow(emit=emit,control=job.control,reviewer=job.reviewer)
        async def work():
            try:
                result=await asyncio.to_thread(workflow.run,job.paths,job.context,OUTPUTS)
                await queue.put({'event':'result','data':result|{'request_id':rid}})
            except Exception as exc:
                await queue.put({'event':'error','message':str(exc)[:250] if isinstance(exc,ValueError) else '本地反演失败，未自动重试',
                                 'request_id':rid,'result_id':workflow.directory.name if workflow.directory else None})
            finally:await queue.put(None)
        task=asyncio.create_task(work())
        try:
            LOGS.add(rid,'INFO','开始本地正向反演；'+('已授权最多一次云端假设复核。' if review_model else '不调用模型，不需要API Key。'))
            yield encode({'event':'phase','phase':'local_inverse','message':'本地正向反演已接收；'+('含已授权可选模型复核。' if review_model else '不发送云端模型请求。'),'request_id':rid})
            while True:
                try:event=await asyncio.wait_for(queue.get(),15)
                except asyncio.TimeoutError:
                    event={'event':'heartbeat','elapsed_s':round(time.monotonic()-job.control.started),'message':'本地计算仍在运行；节点耗时不代表准确率。'}
                if event is None:break
                if event.get('event')=='error':final_status=422
                yield encode(event)
        finally:
            alive=False
            if not task.done():job.control.cancelled.set();final_status=499
            finalize()
    return StreamingResponse(events(),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no','X-Request-ID':rid},
        background=BackgroundTask(background_cleanup))
