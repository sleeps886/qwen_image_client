"""Model boundary replaceable by a fake provider or a future graph runtime."""
from collections.abc import AsyncIterator,Sequence
from pathlib import Path
from typing import Any,Protocol

from backend.app.core.config import model_timeout
from backend.app.models.model_settings import ModelSettings
from backend.app.infrastructure.llm import client,stream_client


class ModelGateway(Protocol):
    def invoke(self,images:Sequence[str],prompt:str,settings:ModelSettings,output_root:Path,mode:str)->dict[str,Any]: ...
    def stream(self,images:Sequence[str],prompt:str,settings:ModelSettings,output_root:Path,mode:str)->AsyncIterator[dict[str,Any]]: ...


class ConfiguredModelGateway:
    def invoke(self,images,prompt,settings,output_root,mode):
        return client.send_images(images,prompt,settings.model,settings.base_url,settings.api_key,
            output_dir=output_root,timeout=model_timeout(),api_format=settings.api_format,
            **({'allow_text_only':True} if mode=='data' else {}))

    async def stream(self,images,prompt,settings,output_root,mode):
        async for event in stream_client.stream_images(images,prompt,settings.model,settings.base_url,
                settings.api_key,output_root,api_format=settings.api_format,
                **({'allow_text_only':True} if mode=='data' else {})):
            yield event
