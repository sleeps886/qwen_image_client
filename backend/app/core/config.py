"""Server-side model settings. .env is resolved relative to this project."""
import os
from backend.app.core.paths import PROJECT_ROOT
from backend.app.models.model_settings import ModelSettings

from dotenv import load_dotenv

ROOT=PROJECT_ROOT
DEFAULT_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
DEFAULT_MODEL='qwen3-vl-plus'


def load_environment(path=None):
    # Never override deployment-provided values or expand credential contents.
    load_dotenv(path or ROOT/'.env',override=False,interpolate=False,encoding='utf-8-sig')




def model_api_format():
    value=os.environ.get('MODEL_API_FORMAT','').strip().lower()
    if not value:
        value='anthropic' if any(os.environ.get(k) for k in ('ANTHROPIC_BASE_URL','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN')) else 'openai'
    if value not in ('openai','anthropic'):raise ValueError('MODEL_API_FORMAT只支持openai或anthropic')
    return value


def model_auth_mode(api_format):
    default='x-api-key' if api_format=='anthropic' and not os.environ.get('ANTHROPIC_AUTH_TOKEN') else 'bearer'
    value=os.environ.get('MODEL_AUTH_MODE',default).strip().lower()
    if value not in ('bearer','x-api-key') or api_format=='openai' and value!='bearer':
        raise ValueError('MODEL_AUTH_MODE应为bearer或x-api-key；OpenAI格式只使用bearer')
    return value


def model_max_tokens():
    try:value=int(os.environ.get('MODEL_MAX_TOKENS','8192'))
    except ValueError:raise ValueError('MODEL_MAX_TOKENS应为1～65536的整数') from None
    if not 1<=value<=65536:raise ValueError('MODEL_MAX_TOKENS应为1～65536的整数')
    return value


def model_settings():
    api_format=model_api_format()
    def value(name,legacy):
        # A deliberately empty new key must not revive an old credential.
        return os.environ.get(name,os.environ.get(legacy,'')).strip()
    if api_format=='anthropic':
        key=os.environ.get('MODEL_API_KEY',os.environ.get('ANTHROPIC_AUTH_TOKEN',os.environ.get('ANTHROPIC_API_KEY',''))).strip()
        return ModelSettings(model=value('MODEL_NAME','ANTHROPIC_MODEL') or DEFAULT_MODEL,
            base_url=value('MODEL_BASE_URL','ANTHROPIC_BASE_URL') or 'https://api.anthropic.com',
            api_key=key,api_format=api_format)
    return ModelSettings(model=value('MODEL_NAME','QWEN_MODEL') or DEFAULT_MODEL,
        base_url=value('MODEL_BASE_URL','QWEN_BASE_URL') or DEFAULT_BASE_URL,
        api_key=value('MODEL_API_KEY','DASHSCOPE_API_KEY'),api_format=api_format)


def model_timeout():
    # Model requests intentionally have no local socket deadline. Ignore the
    # obsolete setting even if it remains in the process environment.
    return None


def model_request_options(api_format='openai'):
    if api_format=='anthropic':
        thinking=os.environ.get('MODEL_THINKING_MODE','auto').strip().lower()
        if thinking not in ('auto','disabled'):
            raise ValueError('MODEL_THINKING_MODE只支持auto或disabled')
        return {'thinking':{'type':'disabled'}} if thinking=='disabled' else {}
    effort=os.environ.get('MODEL_REASONING_EFFORT','').strip().lower()
    if effort and effort not in ('low','high','max'):
        raise ValueError('MODEL_REASONING_EFFORT只支持low/high/max；不需要时留空')
    return {'reasoning_effort':effort} if effort else {}


load_environment()
