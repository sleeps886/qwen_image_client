"""HTTP error translation; domain/services never depend on FastAPI errors."""
from fastapi.responses import JSONResponse
from backend.app.core.exceptions import ApplicationError


async def application_error_handler(request,exc:ApplicationError):
    # Preserve the existing {detail: string/object} API contract.
    return JSONResponse(status_code=exc.status_code,content={'detail':exc.detail})


async def invalid_request(request,exc):
    # Never echo submitted field values, which might contain credentials.
    if request.url.path.startswith('/api/incremental/mixed-sessions/'):
        for error in exc.errors():
            if tuple(error.get('loc',()))==('body','selected_ids') and error.get('type')=='too_long':
                limits=error.get('ctx',{})
                maximum=limits.get('max_length');actual=limits.get('actual_length')
                if type(maximum) is int and type(actual) is int:
                    return JSONResponse(status_code=422,content={'detail':f'本轮选择了{actual}个观测点，最多支持{maximum}个。请缩小选择范围；尚未调用模型，不会自动分批。'})
    return JSONResponse(status_code=422,content={'detail':'字段格式错误，请检查文件、确认选项及表单。'})
