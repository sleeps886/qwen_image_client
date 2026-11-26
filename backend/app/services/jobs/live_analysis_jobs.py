"""Durable send intent + one in-process producer; readers never dispatch."""
import asyncio
import json
from backend.app.core.runtime import SLOTS
from backend.app.services.association.live_tracking import (
    ASSOCIATION_PROMPT, PREDICTION_PROMPT, validate_association, validate_prediction)

TASKS = set()
TERMINAL = {'awaiting_review', 'completed', 'rejected', 'invalid_reply', 'failed', 'output_truncated', 'unknown', 'stale'}


def launch(repo, sid, rid, gateway, settings, output_root):
    task = asyncio.create_task(execute(repo, sid, rid, gateway, settings, output_root))
    TASKS.add(task)
    def done(value):
        TASKS.discard(value)
        if not value.cancelled():
            value.exception()
    task.add_done_callback(done)


async def execute(repo, sid, rid, gateway, settings, output_root):
    slot = SLOTS.acquire(blocking=False)
    if not slot:
        repo.update_run(sid, rid, 'failed', expected_status='authorized', error_type='LocalBusy', error='本地模型资源繁忙，未发送；请显式重新准备。', event={'event': 'status', 'status': 'failed'})
        return
    sent = False
    try:
        if not repo.claim(sid, rid):
            return
        run = repo.get(sid, 'run', rid)
        snapshot = repo.get(sid, 'snapshot', run['snapshot_id'])
        prompt = (ASSOCIATION_PROMPT if run['kind'] == 'analysis' else PREDICTION_PROMPT)+'\n'+json.dumps(snapshot['context'], ensure_ascii=False, separators=(',', ':'))
        sent = True
        repo.update_run(sid, rid, 'running', event={'event': 'status', 'status': 'running'})
        answer = None
        if hasattr(gateway, 'stream'):
            async for item in gateway.stream([], prompt, settings, output_root/rid, 'data'):
                kind = item.get('event')
                if kind == 'model_complete':
                    result = item.get('result', {})
                    if result.get('finish_reason') in ('length', 'max_tokens'):
                        repo.update_run(sid, rid, 'output_truncated', error='上游输出耗尽，保留正文，未自动重试', event={'event': 'status', 'status': 'output_truncated'})
                        return
                    answer = result.get('answer')
                elif kind in ('delta', 'phase'):
                    # Never persist provider output paths/settings/credentials.
                    event = {'event': kind}
                    if kind == 'delta':
                        event['text'] = item.get('text', '')
                    else:
                        event['phase'] = item.get('phase', '')
                    repo.update_run(sid, rid, event=event)
        else:
            result = await asyncio.to_thread(gateway.invoke, [], prompt, settings, output_root/rid, 'data')
            answer = result.get('answer')
            if isinstance(answer, str):
                repo.update_run(sid, rid, event={'event': 'delta', 'text': answer})
            if result.get('finish_reason') in ('length', 'max_tokens'):
                repo.update_run(sid, rid, 'output_truncated', event={'event': 'status', 'status': 'output_truncated'})
                return
        if not isinstance(answer, str):
            raise RuntimeError('MissingUpstreamCompletion')
        current = repo.get(sid, 'run', rid)
        if current['body'] != answer:
            # A buffered provider may omit deltas. Preserve exact actual final body;
            # never label an inconsistent partial stream as complete.
            if not current['body']:
                repo.update_run(sid, rid, event={'event': 'delta', 'text': answer})
            else:
                raise RuntimeError('StreamBodyMismatch')
        try:
            if run['kind'] == 'analysis':
                proposal = validate_association(answer, snapshot)
                repo.complete_analysis(sid,rid,proposal,snapshot)
            else:
                value = validate_prediction(answer, snapshot)
                repo.publish_prediction(sid, rid, value)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            repo.update_run(sid, rid, 'invalid_reply', error_type=type(exc).__name__, error='模型契约或引用校验失败，原文已保留；未自动重试。', event={'event': 'status', 'status': 'invalid_reply'})
    except asyncio.CancelledError:
        repo.update_run(sid, rid, 'unknown', error_type='CancelledError', error='任务中断，外部结果未知；须核对费用，未自动重发。', event={'event': 'status', 'status': 'unknown'})
        raise
    except Exception as exc:
        status = 'unknown' if sent else 'failed'
        repo.update_run(sid, rid, status, error_type=type(exc).__name__, error='模型调用或本地任务未完成；原文已保留，外部结果请核对服务记录，未自动重试。', event={'event': 'status', 'status': status})
    finally:
        SLOTS.release()


async def events(repo, sid, rid, after=0):
    while True:
        items = repo.events(sid, rid, after)
        for item in items:
            after = item['seq']
            yield f'id: {after}\ndata: '+json.dumps(item, ensure_ascii=False)+'\n\n'
        if repo.run_status(sid,rid) in TERMINAL:
            # Drain possible terminal event committed between the reads.
            for item in repo.events(sid, rid, after):
                yield f'id: {item["seq"]}\ndata: '+json.dumps(item, ensure_ascii=False)+'\n\n'
            break
        yield ': keep-alive\n\n'
        await asyncio.sleep(1)

