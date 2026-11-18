"""Local HTTP boundary, limits and request correlation."""
from urllib.parse import urlsplit
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.app.core.logging import LOGS

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


async def local_origin(request:Request,call_next):
    origin=request.headers.get('origin')
    if request.method=='POST' and origin:
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or parsed.netloc!=request.headers.get('host'):
            return JSONResponse(status_code=403,content={'detail':'不允许跨站调用本地服务。'})
    rid=None
    is_stream=request.url.path in ('/api/recognize/stream','/api/invert/stream')
    if request.method=='POST' and request.url.path in ('/api/recognize','/api/recognize/stream','/api/invert','/api/invert/stream'):
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


async def invalid_request(request,exc):
    # Do not echo submitted field values, which might contain credentials.
    return JSONResponse(status_code=422,content={'detail':'字段格式错误，请检查文件、确认选项及表单。'})


