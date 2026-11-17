"""Framework-independent workflow nodes: state in, partial state update out."""
from pathlib import Path
from collections.abc import Callable

from backend.app.models.model_settings import ModelSettings
from backend.app.infrastructure.llm.gateway import ModelGateway
from backend.app.workflows.state import RecognitionState
from backend.app.services.trajectory import render_tracks


def invoke_model_node(state:RecognitionState,*,gateway:ModelGateway,settings:ModelSettings,output_root:Path)->RecognitionState:
    result=gateway.invoke(state['images'],state['prompt'],settings,output_root,state['mode'])
    return {'model_result':result,'answer':result['answer'],'output_dir':result['output_dir']}


async def stream_model_node(state:RecognitionState,*,gateway:ModelGateway,settings:ModelSettings,output_root:Path):
    async for event in gateway.stream(state['images'],state['prompt'],settings,output_root,state['mode']):
        yield event


def render_node(state:RecognitionState,*,renderer:Callable=render_tracks)->RecognitionState:
    document=renderer(state['answer'],state['output_dir'],state.get('context'))
    return {'tracks_document':document}
