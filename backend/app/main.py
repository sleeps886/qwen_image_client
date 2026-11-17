"""Application composition root. Run with python -m backend.app.main."""
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.app.api.router import api_router
from backend.app.core.middleware import UploadLimit,local_origin,invalid_request
from backend.app.core.logging import configure_access_logging


def create_app():
    application=FastAPI(title='千问图片接入服务',version='1.0.0',
        description='本地图片和目标数据解析、预检与模型调用。')
    application.add_middleware(UploadLimit)
    application.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])
    application.middleware('http')(local_origin)
    application.add_exception_handler(RequestValidationError,invalid_request)
    application.include_router(api_router)
    return application


app=create_app()


def main():
    configure_access_logging()
    uvicorn.run(app,host='127.0.0.1',port=8870)


if __name__=='__main__':main()
