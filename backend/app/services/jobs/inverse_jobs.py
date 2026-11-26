"""Prepare local inverse jobs without an HTTP framework dependency."""
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from PIL import Image

from backend.app.core.constants import MAX_FILE_BYTES,MAX_TOTAL_BYTES
from backend.app.core.config import model_settings
from backend.app.core.exceptions import (
    ApplicationError,InputError,BusyError,PayloadTooLargeError,
    ReviewConsentRequired,InvalidImagePair,UnsupportedRendererProfile,
    InvalidImageFormat,ReviewCredentialsRequired,
    InversePreparationError,
)
from backend.app.infrastructure.llm.client import endpoint
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.inputs.input_service import prepare_input
from backend.app.services.jobs.inversion_control import InversionControl


@dataclass
class InverseJob:
    temporary:object
    paths:list
    context:dict
    control:InversionControl
    slots:object
    reviewer:object=None
    finalizer:object=None
    closed:bool=False

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.temporary.cleanup()
        finally:self.slots.release()


def prepare_inverse_job(files,platform,renderer_profile,review_model,consent,*,uploads_dir,slots,final_model=False):
    temporary=None;acquired=False
    try:
        if (review_model or final_model) and not consent:raise ReviewConsentRequired()
        if review_model and final_model:raise InputError('最终模型生成与旧版假设复核不能同时申请')
        if len(files)!=2:raise InvalidImagePair()
        acquired=slots.acquire(blocking=False)
        if not acquired:raise BusyError()
        if renderer_profile!='btr_gui_v1':raise UnsupportedRendererProfile()
        raw=platform.file.read(100001)
        prepared=prepare_input(2,raw,'','images',renderer_profile=renderer_profile)
        if len(prepared.context['platforms'])!=1:raise InputError('当前仅分析单平台：平台文件必须恰好包含一个平台；多平台分析已停用')
        roles=[]
        for upload in files:
            name=Path((upload.filename or '').replace('\\','/')).name
            role=re.search(r'(?:^|_)(hull|tow)(?:\.|_|$)',name,re.I)
            if not role:raise InputError('图片文件名需要保留hull/tow阵列标记')
            roles.append(role.group(1).lower())
            marked=re.fullmatch(r'btr_(.+)_(hull|tow)\.[^.]+',name,re.I)
            if marked and marked.group(1)!=prepared.context['platforms'][0].get('name'):
                raise InputError('图片平台标记与单平台参数不一致，请选择同一样本的图片和参数')
        if sorted(roles)!=['hull','tow']:raise InvalidImagePair()
        uploads_dir.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix='inverse_',dir=uploads_dir)
        paths=[];total=0
        for i,upload in enumerate(files):
            name=(upload.filename or 'image').replace('\\','/').split('/')[-1]
            name=re.sub(r'[^\w.\-]','_',name)[:100] or 'image'
            path=Path(temporary.name)/f'{i:02d}_{name}';size=0
            with path.open('wb') as out:
                while chunk:=upload.file.read(256*1024):
                    size+=len(chunk);total+=len(chunk)
                    if size>MAX_FILE_BYTES or total>MAX_TOTAL_BYTES:raise PayloadTooLargeError()
                    out.write(chunk)
            with Image.open(path) as image:
                if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>16000000:
                    raise InvalidImageFormat()
            paths.append(path)
        reviewer=None
        if review_model or final_model:
            settings=model_settings();endpoint(settings.base_url,settings.api_format)
            if not settings.api_key:raise ReviewCredentialsRequired()
            gateway=ConfiguredModelGateway(timeout_override=75 if final_model else 45)
            def reviewer(images,prompt,directory):
                return gateway.invoke(images,prompt,settings,directory/'review_provider','images')
        return InverseJob(temporary,paths,prepared.context,InversionControl(seconds=240.),slots,reviewer=None if final_model else reviewer,finalizer=reviewer if final_model else None)
    except Exception as exc:
        try:
            if temporary:temporary.cleanup()
        finally:
            if acquired:slots.release()
        if isinstance(exc,ApplicationError):raise
        if isinstance(exc,ValueError):raise InputError(str(exc)[:250]) from None
        # OS paths and arbitrary exception contents are not public input errors.
        raise InversePreparationError() from None
    finally:
        for upload in files:upload.file.close()
        platform.file.close()
