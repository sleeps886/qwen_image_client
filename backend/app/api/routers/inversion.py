"""Local inverse endpoints. Cloud access is an independent explicit opt-in."""
import asyncio
import json
import time
from typing import Annotated

from fastapi import APIRouter,Depends,File,Form,Request,UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from backend.app.core.paths import OUTPUTS_DIR as OUTPUTS,UPLOADS_DIR as UPLOADS
from backend.app.core.exceptions import InverseProcessingError
from backend.app.services.jobs.inverse_jobs import prepare_inverse_job
from backend.app.core.runtime import SLOTS
from backend.app.core.logging import LOGS
from backend.app.agents.forward_inverse_agent import ForwardInverseAgent
from backend.app.api.dependencies import server_config_only

router=APIRouter(tags=['forward-inversion'],dependencies=[Depends(server_config_only)])


def prepare_job(files,platform,renderer_profile,review_model,consent):
    return prepare_inverse_job(files,platform,renderer_profile,review_model,consent,
                               uploads_dir=UPLOADS,slots=SLOTS)


@router.post('/api/invert',summary='完整正向反演；默认只本地计算，模型复核需单独授权')
def invert(request:Request,files:Annotated[list[UploadFile],File()],platform:Annotated[UploadFile,File()],
           renderer_profile:Annotated[str,Form()]='none',review_model:Annotated[bool,Form()]=False,
           consent:Annotated[bool,Form()]=False):
    job=prepare_job(files,platform,renderer_profile,review_model,consent)
    workflow=ForwardInverseAgent(control=job.control,reviewer=job.reviewer)
    try:return workflow.run(job.paths,job.context,OUTPUTS)|{'request_id':request.state.request_id}
    except ValueError as exc:raise InverseProcessingError(str(exc),
        result_id=workflow.directory.name if workflow.directory else None) from None
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
        workflow=ForwardInverseAgent(emit=emit,control=job.control,reviewer=job.reviewer)
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
