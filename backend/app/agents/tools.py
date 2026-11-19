"""Agent model-call tool adapter. No graph construction or retries."""
from backend.app.agents.prompts import OUTPUT_COORDINATE_SYSTEM
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway,ModelGateway
from backend.app.models.model_settings import ModelSettings


class SonarAssociationAgent:
    """One gateway call per invocation; no retries or generated-code execution."""
    output_coordinate_system = OUTPUT_COORDINATE_SYSTEM

    def __init__(self, gateway:ModelGateway|None=None):
        self.gateway=gateway if gateway is not None else ConfiguredModelGateway()

    @staticmethod
    def _check_mode(state):
        if state['mode']!='images':
            raise ValueError('声呐Agent只接受图片任务；单文件绘图使用原直接执行流程')

    def invoke_state(self, state, settings:ModelSettings, output_root):
        self._check_mode(state)
        result=self.gateway.invoke(state['images'],state['prompt'],settings,output_root,'images')
        return {'model_result':result,'answer':result['answer'],'output_dir':result['output_dir']}

    async def stream_state(self, state, settings:ModelSettings, output_root):
        self._check_mode(state)
        async for event in self.gateway.stream(state['images'],state['prompt'],settings,output_root,'images'):
            yield event
