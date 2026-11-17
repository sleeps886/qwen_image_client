"""LangGraph-ready state contract. Secrets stay in runtime dependencies.

Path strings currently refer to request-scoped uploads. Durable resume would
require a separate durable upload repository before adding a checkpointer.
"""
from typing import Any,TypedDict


class RecognitionState(TypedDict,total=False):
    request_id:str
    mode:str
    images:list[str]
    prompt:str
    context:dict[str,Any]|None
    model_result:dict[str,Any]
    answer:str
    output_dir:str
    tracks_document:dict[str,Any]
