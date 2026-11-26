"""Model boundary replaceable by a fake provider or a future graph runtime."""
from collections.abc import AsyncIterator,Sequence
from pathlib import Path
from typing import Any,Protocol

from backend.app.core.config import model_timeout
from backend.app.models.model_settings import ModelSettings
from backend.app.models.association_wire import transport_prompt
from backend.app.infrastructure.llm import client,stream_client


class ModelGateway(Protocol):
    def invoke(self,images:Sequence[str],prompt:str,settings:ModelSettings,output_root:Path,mode:str)->dict[str,Any]: ...
    def stream(self,images:Sequence[str],prompt:str,settings:ModelSettings,output_root:Path,mode:str)->AsyncIterator[dict[str,Any]]: ...


class ConfiguredModelGateway:
    def __init__(self,timeout_override=None,max_prompt_chars=50000,association_transport=False):
        self.timeout_override=timeout_override
        self.max_prompt_chars=max_prompt_chars
        self.association_transport=association_transport

    def invoke(self,images,prompt,settings,output_root,mode):
        if self.association_transport and mode=='data':prompt=transport_prompt(prompt)
        return client.send_images(images,prompt,settings.model,settings.base_url,settings.api_key,
            output_dir=output_root,timeout=self.timeout_override if self.timeout_override is not None else model_timeout(),api_format=settings.api_format,
            **({'allow_text_only':True,'max_prompt_chars':self.max_prompt_chars} if mode=='data' else {}))

    async def stream(self,images,prompt,settings,output_root,mode):
        if self.association_transport and mode=='data':prompt=transport_prompt(prompt)
        async for event in stream_client.stream_images(images,prompt,settings.model,settings.base_url,
                settings.api_key,output_root,api_format=settings.api_format,
                **({'allow_text_only':True,'max_prompt_chars':self.max_prompt_chars} if mode=='data' else {})):
            yield event
