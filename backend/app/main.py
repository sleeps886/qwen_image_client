"""Application composition root. Run with python -m backend.app.main."""
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.app.api.router import api_router
from backend.app.core.middleware import UploadLimit,local_origin
from backend.app.core.logging import configure_access_logging
from backend.app.core.config import settings
from backend.app.core.exceptions import ApplicationError
from backend.app.api.exception_handlers import application_error_handler,invalid_request


def create_app():
    application=FastAPI(title='千问图片接入服务',version='1.0.0',
        description='本地图片和目标数据解析、预检与模型调用。')
    application.add_middleware(UploadLimit)
    application.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.allowed_hosts))
    application.middleware('http')(local_origin)
    application.add_exception_handler(RequestValidationError,invalid_request)
    application.add_exception_handler(ApplicationError,application_error_handler)
    application.include_router(api_router)
    return application


app=create_app()


def main():
    configure_access_logging()
    print('\n网页访问地址（服务启动完成后打开）：',flush=True)
    for host in settings.allowed_hosts:
        if host in ('0.0.0.0','::','[::]'):
            continue
        authority=f'[{host}]' if ':' in host and not host.startswith('[') else host
        print(f'  http://{authority}:{settings.port}/',flush=True)
    print('注意：Uvicorn 下方显示的是监听地址；0.0.0.0 / :: 不能作为网页访问地址。\n',flush=True)
    uvicorn.run(app,host=settings.host,port=settings.port)


if __name__=='__main__':main()
