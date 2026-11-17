"""Domain values; no HTTP uploads or framework objects are stored here."""
from typing import Any,NamedTuple


class PreparedInput(NamedTuple):
    mode:str
    context:dict[str,Any]|None
    prompt:str
    input_data:dict[str,Any]|None
