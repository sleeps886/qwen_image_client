"""Runtime model credentials belong in dependencies, never graph state."""
from dataclasses import dataclass,field


@dataclass(frozen=True)
class ModelSettings:
    model:str
    base_url:str
    api_key:str=field(repr=False)
    api_format:str='openai'
