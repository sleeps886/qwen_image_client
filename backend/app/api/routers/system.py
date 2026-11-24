from fastapi import APIRouter
from fastapi.responses import FileResponse
from backend.app.core.exceptions import NotFoundError
from backend.app.core.paths import PROJECT_ROOT as ROOT, STATIC_DIR
from backend.app.core.config import model_settings, model_timeout
from backend.app.infrastructure.llm.client import DEFAULT_PROMPT, MAX_IMAGES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from backend.app.services.inputs.data_input import DATA_PROMPT,MAX_DATA_BYTES
from backend.app.core.prompts import SPATIAL_OBSERVATION_PROMPT
from urllib.parse import urlsplit

router=APIRouter(tags=["system"])


@router.get('/',include_in_schema=False)
def page():return FileResponse(STATIC_DIR/'index.html')


@router.get('/assets/{filename}',include_in_schema=False)
def asset(filename:str):
    if filename not in ('app.js','style.css'):raise NotFoundError('文件不存在')
    return FileResponse(STATIC_DIR/filename)


@router.get('/api/data-example',summary='下载单文件绘图格式示例，不调用模型')
def data_example():
    return FileResponse(ROOT/'examples/target.json',filename='target.json')


@router.get('/api/health')
def health():
    try:settings=model_settings()
    except ValueError:settings=None  # Invalid optional cloud config cannot hide local inversion.
    return {'status':'ok','service':'qwen-image-client','version':'1.0.0',
            'architecture':'layered-v1','workflow_engine':'langgraph-evidence-v1','langgraph_enabled':True,
            'project_layout':'backend_frontend_v1',
            'knowledge_backend':'sqlite-reviewed-rules','candidate_extraction_supported':True,
            'experimental_measurement_profiles':['btr_gui_v1'],'measurement_default':'legacy',
            'sonar_platform_scope':'single_only','inverse_fit_version':'folded_seed_sync_window_sparse_visibility_gap_v5',
            'model_geometry_gate_version':'source_bound_dual_array_reprojection_gate_v2',
            'forward_inverse_supported':True,'forward_inverse_default_model_calls':0,
            'automatic_paid_retry':False,'full_tracking_implemented':False,
            'continuous_estimation_supported':True,'reprojection_check_supported':True,
            'target_count_audit_supported':True,'node_progress_supported':True,
            'identity_reconciliation_supported':True,
            'candidate_error_method':'pixel_resolution_and_local_ridge_proxy_uncalibrated',
            'streaming_supported':True,
            'key_configured':bool(settings and settings.api_key),
            'api_format':settings.api_format if settings else 'invalid',
            'model_configuration_valid':settings is not None,
            'request_timeout_s':model_timeout(),
            'provider_label':('DMXAPI（第三方服务）' if urlsplit(settings.base_url).hostname in ('www.dmxapi.cn','dmxapi.cn') else 'Anthropic' if settings.api_format=='anthropic' else '阿里云百炼') if settings else '云端配置无效；本地反演仍可使用',
            'default_prompt':SPATIAL_OBSERVATION_PROMPT,
            'spatial_localization_supported':True,'image_output_goal':'spatial_candidates',
            'data_prompt':DATA_PROMPT,'data_file_bytes':MAX_DATA_BYTES,
            'meta_reconstruction_supported':True,
            'limits':{'images':MAX_IMAGES,'file_bytes':MAX_FILE_BYTES,'total_bytes':MAX_TOTAL_BYTES}}


