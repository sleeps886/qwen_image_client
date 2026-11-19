"""HTTP error translation; domain/services never depend on FastAPI errors."""
from fastapi.responses import JSONResponse
from backend.app.core.exceptions import ApplicationError


async def application_error_handler(request,exc:ApplicationError):
    # Preserve the existing {detail: string/object} API contract.
    return JSONResponse(status_code=exc.status_code,content={'detail':exc.detail})


async def invalid_request(request,exc):
    # Never echo submitted field values, which might contain credentials.
    return JSONResponse(status_code=422,content={'detail':'字段格式错误，请检查文件、确认选项及表单。'})
