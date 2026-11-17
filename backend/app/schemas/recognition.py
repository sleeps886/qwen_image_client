from pydantic import BaseModel


class ImageInfo(BaseModel):
    name:str
    bytes:int
    mime:str
    sha256:str


class RecognitionResponse(BaseModel):
    schema_version:str='1.0'
    ok:bool
    dry_run:bool
    result_id:str|None=None
    answer:str|None=None
    usage:dict|None=None
    downloads:dict[str,str]|None=None
    images:list[ImageInfo]|None=None
    message:str|None=None
    trajectory_image:str|None=None
    trajectory_status:str|None=None
    warnings:list[str]|None=None
    request_id:str|None=None
    input_mode:str|None=None
    input_data:dict|None=None
    node_events:list[dict]|None=None
    target_count:dict|None=None


