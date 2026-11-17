import json
import re
import uuid
from typing import Annotated
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from backend.app.core.paths import OUTPUTS_DIR as OUTPUTS
from backend.app.core.runtime import SLOTS
from backend.app.infrastructure.llm.client import MAX_FILE_BYTES
from backend.app.services.comparison import compare_images
from backend.app.services.evaluation import evaluate

router=APIRouter(tags=["evaluation"])


@router.post('/api/compare',summary='本地比较AI轨迹图与仿真参考图，不调用模型')
def compare(
    result_id:Annotated[str,Form()],
    reference:Annotated[UploadFile,File(description='同一样本truth/btr_A_track.png')],
    reference_kind:Annotated[str,Form(description='参考图类型：position或bearing_time')]='position',
):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id):raise HTTPException(400,'结果编号无效')
    root=OUTPUTS/result_id
    if not root.is_dir() or root.is_symlink():raise HTTPException(404,'AI结果不存在')
    prediction=root/'estimated_track.png'
    tracks=root/'tracks.json'
    if not prediction.is_file() or not tracks.is_file():raise HTTPException(400,'本次没有生成可比较的AI位置轨迹图')
    if prediction.is_symlink() or tracks.is_symlink():raise HTTPException(400,'不接受链接文件')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'服务繁忙，请稍后比较')
    try:
        frame=json.loads(tracks.read_text(encoding='utf-8')).get('coordinate_system')
        if reference_kind not in ('position','bearing_time'):raise HTTPException(400,'参考图类型无效')
        predicted_kind='position' if frame in ('local_xy_m','lon_lat') else 'bearing_time' if frame=='bearing_time' else None
        if not predicted_kind:raise HTTPException(400,'AI轨迹坐标系无效')
        if predicted_kind!=reference_kind:raise HTTPException(400,'两图坐标类型不同：方位—时间图不能与地理位置轨迹图评分')
        raw=reference.file.read(MAX_FILE_BYTES+1)
        if len(raw)>MAX_FILE_BYTES:raise HTTPException(413,'参考图片不能超过10MiB')
        # Unique subdirectory permits repeated comparisons without overwriting evidence.
        cid=uuid.uuid4().hex
        directory=root/'comparisons'/cid;directory.mkdir(parents=True)
        report=compare_images(raw,prediction.read_bytes(),directory)
        report.update(result_id=result_id,comparison_id=cid,reference_kind=reference_kind,predicted_kind=predicted_kind)
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        url=f'/api/comparisons/{result_id}/{cid}'
        return {'ok':True,**report,'overlay_url':url+'/comparison_overlay.png','report_url':url+'/report.json'}
    except (ValueError,OSError):
        raise HTTPException(400,'图像比较失败：请确认参考图为黑底彩色轨迹PNG/JPEG，而不是声呐图；并检查本地写入权限。') from None
    finally:
        reference.file.close();SLOTS.release()


@router.post('/api/evaluate',summary='本地结构化评测：目标计数、轨迹匹配、位置误差与时间覆盖率')
def evaluate_result(
    result_id:Annotated[str,Form()],
    semantic:Annotated[UploadFile|None,File(description='同一样本semantic.json，仅本地参考')]=None,
    truth:Annotated[UploadFile|None,File(description='同平台逐时刻btr_A_truth.jsonl')]=None,
    gate_m:Annotated[float,Form(ge=1,le=100000)]=1000,
    max_gap_s:Annotated[float,Form(ge=.1,le=600)]=10,
):
    if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{8}',result_id):raise HTTPException(400,'AI结果编号无效')
    root=OUTPUTS/result_id;path=root/'tracks.json'
    if root.is_symlink() or path.is_symlink() or not path.is_file():raise HTTPException(404,'缺少AI轨迹数据')
    if not SLOTS.acquire(blocking=False):raise HTTPException(429,'服务繁忙，请稍后评测')
    try:
        semantic_raw=semantic.file.read(2*1024*1024+1) if semantic else None
        truth_raw=truth.file.read(16*1024*1024+1) if truth else None
        if semantic_raw and len(semantic_raw)>2*1024*1024:raise HTTPException(413,'semantic不能超过2MiB')
        if truth_raw and len(truth_raw)>16*1024*1024:raise HTTPException(413,'truth不能超过16MiB')
        report=evaluate(json.loads(path.read_text(encoding='utf-8')),semantic_raw,truth_raw,gate_m,max_gap_s)
        cid=uuid.uuid4().hex;directory=root/'comparisons'/cid;directory.mkdir(parents=True)
        report.update(result_id=result_id,evaluation_id=cid)
        (directory/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        return {'ok':True,**report,'report_url':f'/api/comparisons/{result_id}/{cid}/evaluation.json'}
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        # Messages describe schema problems, never echo the uploaded document.
        detail=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,(json.JSONDecodeError,UnicodeDecodeError)) else '参考文件或AI轨迹格式无效'
        raise HTTPException(400,detail) from None
    except OSError:raise HTTPException(500,'评测文件读写失败，请检查目录权限') from None
    finally:
        SLOTS.release()
        if semantic:semantic.file.close()
        if truth:truth.file.close()


