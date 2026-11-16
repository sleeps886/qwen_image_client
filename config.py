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
    return ModelSettings(model=os.environ.get('QWEN_MODEL','').strip() or DEFAULT_MODEL,
        base_url=os.environ.get('QWEN_BASE_URL','').strip() or DEFAULT_BASE_URL,
        api_key=os.environ.get('DASHSCOPE_API_KEY','').strip())


load_environment()
