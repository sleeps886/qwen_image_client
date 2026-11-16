"""Server-side model settings. .env is resolved relative to this project."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
DEFAULT_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
DEFAULT_MODEL='qwen3-vl-plus'


def load_environment(path=None):
    # Never override deployment-provided values or expand credential contents.
    load_dotenv(path or ROOT/'.env',override=False,interpolate=False,encoding='utf-8-sig')


@dataclass(frozen=True)
class ModelSettings:
    model:str
    base_url:str
    api_key:str=field(repr=False)


def model_settings():
    def value(name,legacy):
        # A deliberately empty new key must not revive an old credential.
        return os.environ.get(name,os.environ.get(legacy,'')).strip()
    return ModelSettings(model=value('MODEL_NAME','QWEN_MODEL') or DEFAULT_MODEL,
        base_url=value('MODEL_BASE_URL','QWEN_BASE_URL') or DEFAULT_BASE_URL,
        api_key=value('MODEL_API_KEY','DASHSCOPE_API_KEY'))


def model_timeout():
    # Model requests intentionally have no local socket deadline. Ignore the
    # obsolete setting even if it remains in the process environment.
    return None


load_environment()
