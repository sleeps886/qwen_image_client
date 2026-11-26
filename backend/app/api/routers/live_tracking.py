"""Opt-in v2 live workflow; legacy mixed sessions/files are never auto-migrated."""
import json
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from backend.app.core.config import model_settings
from backend.app.core.exceptions import InputError
from backend.app.core.paths import OUTPUTS_DIR, STATIC_DIR
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.models.live_tracking import CreateLive, IngestLive, PrepareLive, AuthorizeLive, ReviewLive, ConfigureLive, MigrateLive, RetractLive
from backend.app.repositories.live_observation_repository import LiveRepository, uid
from backend.app.services.association.live_tracking import prepare_live
from backend.app.services.association.incremental_evidence import digest
from backend.app.services.jobs.live_analysis_jobs import launch, events

router = APIRouter(tags=['live tracking v2'])
LIVE_DB = OUTPUTS_DIR/'live_tracking'/'ledger.sqlite3'


def repository():
    return LiveRepository(LIVE_DB)


def live_gateway():
    return ConfiguredModelGateway(max_prompt_chars=None)


def model_reference(settings=None):
    try:
        settings = settings or model_settings()
    except ValueError:
        return {'configuration_invalid': True}
    return {'model': settings.model, 'provider_host': urlsplit(settings.base_url).hostname,
            'endpoint_digest': digest(settings.base_url), 'api_format': settings.api_format}


@router.get('/live', include_in_schema=False)
def page(diagnostic:bool=False):
    # User workflow stays in the existing workbench. Keep the old experimental
    # view only for development/compatibility tests, never as a second entry.
    return FileResponse(STATIC_DIR/'live.html') if diagnostic else RedirectResponse('/incremental')


@router.get('/api/incremental/live-sessions')
def sessions(repo=Depends(repository)):
    return [{'session_id': s['session_id'], 'title': s['title'], 'timeline_id': s['timeline_id'], 'created_at': s['created_at']} for s in repo.list_sessions()]


@router.post('/api/incremental/live-sessions')
def create(body: CreateLive, repo=Depends(repository)):
    return repo.create(body.timeline_id, body.title, body.configuration)


@router.post('/api/incremental/live-sessions/migrate')
def migrate(body: MigrateLive, repo=Depends(repository)):
    candidates = body.dataset.get('candidates')
    if body.dataset.get('evaluation_only') or not isinstance(candidates, list) or not candidates:
        raise InputError('请选择原始观测数据；真值不能导入')
    state = repo.create(body.timeline_id, body.title, body.configuration)
    result = repo.ingest(state['session_id'], {'idempotency_key': uid('migration'), 'source_id': 'explicit-json-copy', 'timeline_id': body.timeline_id, 'observations': candidates})
    repo.archive_legacy(state['session_id'], body.legacy_history)
    return {'session': repo.state(state['session_id']), 'receipt': result, 'legacy_unchanged': True,
            'note': '复制测量并保存只读旧结果存档/编号映射，不把旧身份或预测当新结论；旧JSON未修改。'}


@router.get('/api/incremental/live-sessions/{sid}')
def state(sid: str, repo=Depends(repository)):
    return repo.state(sid)


@router.post('/api/incremental/live-sessions/{sid}/observations')
def ingest(sid: str, body: IngestLive, repo=Depends(repository)):
    return repo.ingest(sid, body.model_dump())


@router.get('/api/incremental/live-sessions/{sid}/observations')
def observations(sid: str, after_seq: int = Query(default=0, ge=0), repo=Depends(repository)):
    return repo.observations(sid, after_seq)


@router.post('/api/incremental/live-sessions/{sid}/configuration')
def configure(sid: str, body: ConfigureLive, repo=Depends(repository)):
    return repo.configure(sid, body.expected_state_revision, body.configuration)


@router.post('/api/incremental/live-sessions/{sid}/prepare')
def prepare(sid: str, body: PrepareLive, repo=Depends(repository)):
    if body.purpose != 'association':
        raise InputError('预测请使用独立的predictions/prepare入口')
    return prepare_live(repo, sid, body.model_dump(), model_reference())


@router.post('/api/incremental/live-sessions/{sid}/predictions/prepare')
def prepare_prediction(sid: str, body: PrepareLive, repo=Depends(repository)):
    if body.purpose == 'association':
        raise InputError('预测需明确因果预测或事后重建用途')
    return prepare_live(repo, sid, body.model_dump(), model_reference())


async def authorize(sid, body, kind, repo, gateway):
    snapshot = repo.get(sid, 'snapshot', body.snapshot_id)
    if kind == 'prediction' and not snapshot.get('prediction_allowed'):
        raise InputError(snapshot.get('prediction_block_reason', '当前快照不允许预测'))
    try:
        settings = model_settings()
    except ValueError:
        raise InputError('模型配置无效；免费接入和准备不受影响') from None
    if not settings.api_key:
        raise InputError('尚未配置模型服务；不会发送')
    reference = model_reference(settings)
    if snapshot['context'].get('model_reference') != reference:
        raise InputError('模型配置已改变，请重新免费准备并核对授权')
    run, created = repo.authorize(sid, body.model_dump(), kind, reference)
    if created:
        launch(repo, sid, run['run_id'], gateway, settings, OUTPUTS_DIR/'live_tracking'/'model')
    return run


@router.post('/api/incremental/live-sessions/{sid}/analyses')
async def analyse(sid: str, body: AuthorizeLive, repo=Depends(repository), gateway=Depends(live_gateway)):
    return await authorize(sid, body, 'analysis', repo, gateway)


@router.post('/api/incremental/live-sessions/{sid}/predictions')
async def predict(sid: str, body: AuthorizeLive, repo=Depends(repository), gateway=Depends(live_gateway)):
    return await authorize(sid, body, 'prediction', repo, gateway)


@router.get('/api/incremental/live-sessions/{sid}/analyses/{rid}')
def run(sid: str, rid: str, repo=Depends(repository)):
    return repo.get(sid, 'run', rid)


@router.get('/api/incremental/live-sessions/{sid}/analyses/{rid}/events')
def stream(sid: str, rid: str, after_seq: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None), repo=Depends(repository)):
    repo.get(sid, 'run', rid)
    if last_event_id:
        try:
            after_seq = max(after_seq, int(last_event_id))
        except ValueError:
            raise InputError('事件序号无效') from None
    return StreamingResponse(events(repo, sid, rid, after_seq), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/api/incremental/live-sessions/{sid}/analyses/{rid}/review')
def review(sid: str, rid: str, body: ReviewLive, repo=Depends(repository)):
    return repo.review(sid, rid, body.model_dump())


@router.get('/api/incremental/live-sessions/{sid}/export')
def export(sid: str, repo=Depends(repository)):
    return JSONResponse(repo.export(sid), headers={'Content-Disposition': 'attachment; filename="live-ledger.json"'})


@router.get('/api/incremental/live-sessions/{sid}/predictions/{prediction_id}')
def prediction(sid: str, prediction_id: str, repo=Depends(repository)):
    return repo.get(sid, 'prediction', prediction_id)


@router.get('/api/incremental/live-sessions/{sid}/snapshots/{snapshot_id}')
def snapshot(sid: str, snapshot_id: str, repo=Depends(repository)):
    return repo.get(sid, 'snapshot', snapshot_id)


@router.post('/api/incremental/live-sessions/{sid}/retractions')
def retract(sid: str, body: RetractLive, repo=Depends(repository)):
    return repo.retract(sid, body.expected_state_revision, body.stable_ids, body.reason)
