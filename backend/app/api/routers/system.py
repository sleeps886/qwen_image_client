from fastapi import APIRouter, Request, HTTPException
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
    if filename not in ('generator.js','app.js','style.css','incremental.js','workspace-cache.js','joint-prediction.js','joint-prediction.css','mixed-workspace.js','mixed-workspace.css','track-connections.js','point-sampling.js','observation-diagnostics.js','live.js','live.css','evidence-neighborhood.js'):raise NotFoundError('文件不存在')
    return FileResponse(STATIC_DIR/filename)


@router.get('/api/data-example',summary='下载单文件绘图格式示例，不调用模型')
def data_example():
    return FileResponse(ROOT/'examples/target.json',filename='target.json')


@router.get('/api/health')
def health():
    try:settings=model_settings()
    except ValueError:settings=None  # Invalid optional cloud config cannot hide local inversion.
    return {'status':'ok','service':'qwen-image-client','version':'1.0.0',
            'mixed_sequential_protocol':'sequential-prefix-v1',
            'mixed_batch_snapshot_protocol':'batch-snapshot-v1',
            'mixed_batch_retry_protocol':'retry-v1',
            'mixed_spatial_card_protocol':'model-selected-spatial-cards-v1',
            'mixed_continuation_policy':'tentative-local-v1',
            'mixed_sequential_spatial':'sequential-spatial-v1',
            'architecture':'layered-v1','workflow_engine':'langgraph-evidence-v1','langgraph_enabled':True,
            'project_layout':'backend_frontend_v1',
            'knowledge_backend':'sqlite-reviewed-rules','candidate_extraction_supported':True,
            'experimental_measurement_profiles':['btr_gui_v1'],'measurement_default':'legacy',
            'sonar_platform_scope':'single_only','inverse_fit_version':'folded_seed_refinement_budget_gate_v8',
            'inverse_budget_s':240,'inverse_profile_budget_s':120,
            'model_geometry_gate_version':'source_bound_dual_array_reprojection_gate_v2',
            'mixed_identity_protocol':'joint-candidate-v1',
            'mixed_association_transport':'lossless-table-v1',
            'mixed_input_policy':'accept-sparse-v1',
            'mixed_workspace_input':'workspace-ledger-v1',
            'mixed_plot_preflight':'purpose-capability-v1',
            'live_tracking_protocol':'association-compact-v2',
            'live_tracking_storage':'sqlite-ledger-v1',
            'live_prediction_capability':'bearing-only',
            'mixed_review_protocol':'typed-review-scope-v2',
            'mixed_spatial_gate':'preconfirmed-temporal-holdout-v1',
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




@router.get('/generator',include_in_schema=False)
def generator_page():return FileResponse(STATIC_DIR/'generator.html')


@router.post('/api/generator/start',summary='显式启动本机数据生成工具，不调用模型')
def start_generator(request:Request):
    import subprocess,sys,time,urllib.request
    tool=ROOT.parent/'btr_gens'/'btr_gens'/'btr_gui_server.py'
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'仅允许本页面启动工具')
    if not tool.is_file():raise HTTPException(404,'未找到本机btr_gens工具目录')
    def running():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/',timeout=.5) as r:
                return 'BTR' in r.read(20000).decode('utf-8','ignore')
        except OSError:return False
    if running():return {'message':'工具已运行；修改生成器代码后请先关闭旧工具，再重新启动。'}
    log_dir=ROOT/'logs';log_dir.mkdir(exist_ok=True)
    with (log_dir/'generator.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,str(tool)],cwd=tool.parent,stdout=log,stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(30):
        if running():return {'message':'数据生成工具已启动。生成后下载数据点文件及独立真值文件。'}
        if process.poll() is not None:raise HTTPException(503,'工具启动失败，请检查logs/generator.log；未自动重试。')
        time.sleep(.2)
    raise HTTPException(503,'工具尚未就绪，请稍后刷新；未重复启动。')
