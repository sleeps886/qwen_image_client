from fastapi import Request
from backend.app.core.exceptions import InputError
from backend.app.services.inputs.input_service import prepare_input
from backend.app.services.inputs.data_input import MAX_DATA_BYTES
from backend.app.agents.sonar_agent import SonarAgent


def get_sonar_agent()->SonarAgent:
    """Create the complete sonar Agent; tests may override this dependency."""
    return SonarAgent()


def prepare_selected_input(files,platform,prompt,input_mode,renderer_profile='none'):
    """Translate multipart uploads to plain bytes before entering the service."""
    limit=MAX_DATA_BYTES if input_mode=='data' or not files else 100000
    raw=platform.file.read(limit+1) if platform is not None else None
    filename=(platform.filename or 'data.json') if platform is not None else 'data.json'
    return prepare_input(len(files),raw,prompt,input_mode,filename,renderer_profile=renderer_profile)


async def server_config_only(request:Request):
    form=await request.form()
    if any(key in form for key in ('model','base_url','api_key','x_dashscope_key','api_format','auth_mode')) or 'x-dashscope-key' in request.headers:
        raise InputError('模型连接配置已迁移至服务端.env，不接受请求覆盖。')


