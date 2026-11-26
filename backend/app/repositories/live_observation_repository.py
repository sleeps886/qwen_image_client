"""SQLite authority for v2 facts, frozen snapshots, projections and paid intent.

Connections/transactions are short and never held across numerical/cloud work.
Legacy JSON remains untouched. Single-process deployment is the supported mode.
"""
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from backend.app.core.exceptions import InputError, NotFoundError, ApplicationError
from backend.app.services.inputs.live_observation_input import normalize_live, audit_value
from backend.app.services.association.incremental_evidence import digest, DEFAULT_ALGORITHM
from backend.app.services.localization.platform_motion import platform_metadata


class LiveConflict(ApplicationError):
    status_code = 409


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix+'-'+uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def configuration(raw):
    if set(raw)-{'platform_config', 'algorithm'}:
        raise InputError('仅允许平台配置与算法参数，不接收目标标签或认证信息')
    result = {'algorithm': dict(DEFAULT_ALGORITHM)}
    if raw.get('platform_config'):
        platform = platform_metadata(raw['platform_config'])
        if not platform:
            raise InputError('平台配置无效')
        names = [p['name'] for p in platform['platforms']]
        if len(names) != len(set(names)):
            raise InputError('平台名称不得重复')
        for target, source in zip(platform['platforms'], raw['platform_config']['platforms']):
            color = source.get('color')
            if isinstance(color,str) and len(color)==7 and color.startswith('#') and all(c in '0123456789abcdefABCDEF' for c in color[1:]):
                target['color'] = color
        result['platform_config'] = platform
    algorithm = raw.get('algorithm', {})
    if not isinstance(algorithm, dict) or set(algorithm)-set(DEFAULT_ALGORITHM):
        raise InputError('算法配置字段无效')
    for key, value in algorithm.items():
        if key.endswith('_times') or key == 'candidate_budget_per_window':
            if type(value) is not int or not 1 <= value <= 64:
                raise InputError('窗口/步长/候选预算须为1至64的整数')
        elif type(value) not in (float, int) or not 0 < value <= 180:
            raise InputError('候选门限无效')
        result['algorithm'][key] = value
    if result['algorithm']['window_times'] < 4 or result['algorithm']['stride_times'] > result['algorithm']['window_times']:
        raise InputError('窗口至少4个时刻，步长不得大于窗口')
    return result


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class LiveRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise InputError('账本路径不能是符号链接')
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS live_sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                    sid TEXT, oid TEXT PRIMARY KEY, source TEXT, source_oid TEXT,
                    receipt_seq INTEGER, event_time REAL, fingerprint TEXT, body TEXT,
                    UNIQUE(sid,source,source_oid));
                CREATE INDEX IF NOT EXISTS obs_seq ON observations(sid,receipt_seq);
                CREATE INDEX IF NOT EXISTS obs_time ON observations(sid,event_time);
                CREATE TABLE IF NOT EXISTS receipts(sid TEXT, seq INTEGER, body TEXT, PRIMARY KEY(sid,seq));
                CREATE TABLE IF NOT EXISTS requests(sid TEXT, key TEXT, fingerprint TEXT, body TEXT, PRIMARY KEY(sid,key));
                CREATE TABLE IF NOT EXISTS entities(sid TEXT, kind TEXT, id TEXT PRIMARY KEY, body TEXT);
                CREATE INDEX IF NOT EXISTS entity_session ON entities(sid,kind);
                CREATE TABLE IF NOT EXISTS run_events(run_id TEXT, seq INTEGER, body TEXT, PRIMARY KEY(run_id,seq));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _session(self, db, sid):
        row = db.execute('SELECT body FROM live_sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise NotFoundError('实时会话不存在')
        return json.loads(row['body'])

    def _save(self, db, state):
        db.execute('UPDATE live_sessions SET body=? WHERE id=?', (dump(state), state['session_id']))

    def _put(self, db, sid, kind, eid, body):
        db.execute('INSERT INTO entities VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (sid, kind, eid, dump(body)))

    def _get(self, db, sid, kind, eid):
        row = db.execute('SELECT body FROM entities WHERE sid=? AND kind=? AND id=?', (sid, kind, eid)).fetchone()
        if not row:
            raise NotFoundError('版本或任务不存在')
        return json.loads(row['body'])

    def _list(self, db, sid, kind):
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM entities WHERE sid=? AND kind=? ORDER BY rowid', (sid, kind))]

    def _public_run(self, db, run):
        value = dict(run)
        value['body'] = ''.join(event.get('text','') for row in db.execute('SELECT body FROM run_events WHERE run_id=? ORDER BY seq',(run['run_id'],)) if (event:=json.loads(row[0])).get('event') == 'delta')
        return value

    def _invalidate_proposals(self, db, sid, except_id=None):
        for run in self._list(db,sid,'run'):
            if run['status'] == 'awaiting_review' and run['run_id'] != except_id:
                run['status'] = 'stale'
                self._put(db,sid,'run',run['run_id'],run)
                self._event(db,run['run_id'],{'event':'status','status':'stale'})

    def create(self, timeline_id, title, raw_configuration):
        config = configuration(raw_configuration)
        sid, cfg = uid('live'), uid('cfg')
        state = {'session_id': sid, 'timeline_id': timeline_id, 'title': title, 'created_at': now(),
                 'observation_revision': 0, 'state_revision': 0, 'receipt_seq': 0,
                 'configuration_id': cfg, 'configuration': config, 'projection': {}, 'active': True}
        with self.transaction() as db:
            db.execute('INSERT INTO live_sessions VALUES(?,?)', (sid, dump(state)))
            self._put(db, sid, 'configuration', cfg, {'configuration_id': cfg, 'configuration': config, 'created_at': now(), 'known_seq': 0})
        return state

    def list_sessions(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM live_sessions ORDER BY rowid DESC')]

    def state(self, sid):
        with self.connect() as db:
            state = self._session(db, sid)
            count = db.execute('SELECT count(*) FROM observations WHERE sid=?', (sid,)).fetchone()[0]
            scope = set(state['projection'].get('scope_ids', []))
            state['accepted_count'] = count
            state['unprocessed_count'] = count-len(scope)
            projection = state['projection']
            state['ownership_counts'] = {
                'adopted':sum(len(g['observation_ids']) for g in projection.get('stream_groups',[])),
                'waiting_evidence':sum(len(g['observation_ids']) for g in projection.get('unresolved_groups',[]) if not g.get('possible_group_refs')),
                'ambiguous':sum(len(g['observation_ids']) for g in projection.get('unresolved_groups',[]) if g.get('possible_group_refs')),
                'unprocessed':state['unprocessed_count']}
            state['receipt_counts'] = dict(accepted=0, duplicate=0, quarantined=0, conflict=0, configuration=0, review=0)
            for row in db.execute('SELECT body FROM receipts WHERE sid=?', (sid,)):
                state['receipt_counts'][json.loads(row[0])['status']] += 1
            state['runs'] = self._list(db, sid, 'run')
            state['predictions'] = self._list(db, sid, 'prediction')
            return state

    def ingest(self, sid, request):
        # Nonfinite numbers are quarantined per row, not allowed to turn an
        # otherwise valid batch into a 500 while hashing/auditing the raw input.
        fingerprint = digest(json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=True))
        with self.transaction() as db:
            state = self._session(db, sid)
            old = db.execute('SELECT * FROM requests WHERE sid=? AND key=?', (sid, request['idempotency_key'])).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise LiveConflict('同一接入请求键对应了不同载荷')
                return json.loads(old['body'])
            results = []
            accepted = 0
            for raw in request['observations']:
                state['receipt_seq'] += 1
                receipt = {'receipt_seq': state['receipt_seq'], 'received_at': now(), 'status': 'quarantined'}
                try:
                    if request['timeline_id'] != state['timeline_id']:
                        raise ValueError('时间基准不一致；请使用独立会话，不自动对齐时钟')
                    point = normalize_live(raw)
                    identity = (sid, request['source_id'], point['source_observation_id'])
                    old = db.execute('SELECT * FROM observations WHERE sid=? AND source=? AND source_oid=?', identity).fetchone()
                    if old:
                        receipt.update(status='duplicate' if old['fingerprint'] == digest(point) else 'conflict', observation_id=old['oid'])
                        if receipt['status'] == 'conflict':
                            receipt['reason'] = '来源编号已存在且数值不同；原观测未覆盖'
                    else:
                        oid = uid('obs')
                        body = {**point, 'observation_id': oid, 'source_id': request['source_id'],
                                'timeline_id': state['timeline_id'], 'platform_config_version': state['configuration_id'],
                                'receipt_seq': receipt['receipt_seq'], 'received_at': receipt['received_at']}
                        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)', (*identity[:1], oid, *identity[1:], receipt['receipt_seq'], point['event_time'], digest(point), dump(body)))
                        receipt.update(status='accepted', observation_id=oid)
                        accepted += 1
                except ValueError as exc:
                    receipt['reason'] = str(exc)
                # Original row remains exportable, never enters blind model context.
                audit = {**receipt, 'raw_json': json.dumps(audit_value(raw), ensure_ascii=False, allow_nan=True), 'source_id': request['source_id'], 'timeline_id': request['timeline_id']}
                db.execute('INSERT INTO receipts VALUES(?,?,?)', (sid, receipt['receipt_seq'], dump(audit)))
                results.append(receipt)
            if accepted:
                state['observation_revision'] += 1
            self._save(db, state)
            result = {'results': results, 'accepted': accepted, 'observation_revision': state['observation_revision'], 'receipt_seq': state['receipt_seq'], 'model_calls': 0}
            db.execute('INSERT INTO requests VALUES(?,?,?,?)', (sid, request['idempotency_key'], fingerprint, dump(result)))
            return result

    def observations(self, sid, after_seq=0):
        with self.connect() as db:
            self._session(db, sid)
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM observations WHERE sid=? AND receipt_seq>? ORDER BY receipt_seq', (sid, after_seq))]

    def get(self, sid, kind, eid):
        with self.connect() as db:
            value = self._get(db, sid, kind, eid)
            return self._public_run(db,value) if kind == 'run' else value

    def items(self, sid, kind):
        with self.connect() as db:
            return self._list(db, sid, kind)

    def freeze(self, sid, state, snapshot):
        with self.transaction() as db:
            current = self._session(db, sid)
            if current['state_revision'] != state['state_revision'] or current['configuration_id'] != state['configuration_id']:
                raise LiveConflict('准备期间投影或配置已改变，请重新免费准备')
            self._put(db, sid, 'snapshot', snapshot['snapshot_id'], snapshot)
        return snapshot

    def configure(self, sid, expected, raw):
        value = configuration(raw)
        with self.transaction() as db:
            state = self._session(db, sid)
            if expected != state['state_revision']:
                raise LiveConflict('配置基础版本过期')
            eid = uid('cfg')
            state['receipt_seq'] += 1
            state.update(configuration_id=eid, configuration=value, state_revision=expected+1)
            self._put(db, sid, 'configuration', eid, {'configuration_id': eid, 'configuration': value, 'created_at': now(), 'known_seq': state['receipt_seq']})
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (sid, state['receipt_seq'], dump({'receipt_seq': state['receipt_seq'], 'received_at': now(), 'status': 'configuration', 'configuration_id': eid})))
            self._save(db, state)
            self._invalidate_proposals(db,sid)
        return state

    def authorize(self, sid, body, kind, model_reference):
        with self.transaction() as db:
            state = self._session(db, sid)
            snapshot = self._get(db, sid, 'snapshot', body['snapshot_id'])
            previous = self._list(db, sid, 'run')
            signature = digest(body)
            for run in previous:
                if run['idempotency_key'] == body['idempotency_key']:
                    if run['request_digest'] != signature:
                        raise LiveConflict('相同任务键对应不同授权')
                    return run, False
            if not body['consent']:
                raise InputError('必须明确授权本快照的一次模型调用与费用')
            if snapshot['kind'] != kind or snapshot['context_digest'] != body['context_digest']:
                raise LiveConflict('快照用途或摘要不匹配')
            if state['state_revision'] != body['expected_state_revision'] or snapshot['base_state_revision'] != state['state_revision'] or snapshot['configuration_id'] != state['configuration_id']:
                raise LiveConflict('投影或配置已改变，请重新免费准备')
            same = [r for r in previous if r['snapshot_id'] == snapshot['snapshot_id']]
            retried = {r.get('rerun_of') for r in previous if r.get('rerun_of')}
            blocked = [r for r in previous if r['status'] in ('authorized', 'dispatching', 'running', 'unknown') and r['run_id'] not in retried]
            retry = next((r for r in previous if r['run_id'] == body.get('rerun_of')), None)
            if body.get('rerun_of') and (not retry or retry['kind'] != kind or retry['status'] not in ('unknown','invalid_reply','failed','output_truncated') or not body['duplicate_fee_ack']):
                raise LiveConflict('重跑引用无效、用途不同或缺少费用风险确认')
            if same or blocked:
                if not retry or retry['kind'] != kind or retry['status'] not in ('unknown', 'invalid_reply', 'failed', 'output_truncated') or not body['duplicate_fee_ack']:
                    raise LiveConflict('已有发送记录或未知结果；请核对服务记录并明确手动重跑风险，不能换请求键重发')
                if any(r['run_id'] != retry['run_id'] for r in blocked):
                    raise LiveConflict('其他任务仍运行或结果未知')
                if any(r.get('rerun_of') == retry['run_id'] for r in previous):
                    raise LiveConflict('此失败任务已有重跑记录，请读取该记录')
            rid = uid('run')
            run = {'run_id': rid, 'snapshot_id': snapshot['snapshot_id'], 'kind': kind,
                   'status': 'authorized', 'created_at': now(), 'idempotency_key': body['idempotency_key'],
                   'request_digest': signature, 'base_state_revision': state['state_revision'],
                   'configuration_id': snapshot['configuration_id'], 'model_reference': model_reference,
                   'body': '', 'rerun_of': body.get('rerun_of'), 'automatic_retry': False}
            self._put(db, sid, 'run', rid, run)
            self._event(db, rid, {'event': 'status', 'status': 'authorized'})
            return run, True

    def _event(self, db, rid, value):
        seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM run_events WHERE run_id=?', (rid,)).fetchone()[0]
        db.execute('INSERT INTO run_events VALUES(?,?,?)', (rid, seq, dump({**value, 'seq': seq})))

    def update_run(self, sid, rid, status=None, event=None, expected_status=None, **fields):
        with self.transaction() as db:
            run = self._get(db, sid, 'run', rid)
            if expected_status is not None and run['status'] != expected_status:
                return run
            if status:
                run['status'] = status
            run.update(fields)
            if event:
                if event.get('event') == 'delta':
                    count = run.get('body_chars',0)+len(event.get('text', ''))
                    if count > 4_000_000:
                        raise ValueError('模型正文超过4百万字符资源边界；未裁剪输入')
                    run['body_chars'] = count
                self._event(db, rid, event)
            self._put(db, sid, 'run', rid, run)
            return run

    def complete_analysis(self,sid,rid,proposal,snapshot):
        with self.transaction() as db:
            state,run = self._session(db,sid),self._get(db,sid,'run',rid)
            stale = state['state_revision'] != run['base_state_revision'] or state['configuration_id'] != run['configuration_id']
            run.update(status='stale' if stale else 'awaiting_review',proposal=proposal,
                       scope_ids=snapshot['scope_ids'],event_range=snapshot['event_range'])
            self._put(db,sid,'run',rid,run)
            self._event(db,rid,{'event':'status','status':run['status']})
            return run

    def claim(self, sid, rid):
        with self.transaction() as db:
            run = self._get(db, sid, 'run', rid)
            if run['status'] != 'authorized':
                return False
            run.update(status='dispatching', dispatched_at=now())
            self._put(db, sid, 'run', rid, run)
            self._event(db, rid, {'event': 'status', 'status': 'dispatching'})
            return True

    def events(self, sid, rid, after=0):
        with self.connect() as db:
            self._get(db, sid, 'run', rid)
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM run_events WHERE run_id=? AND seq>? ORDER BY seq', (rid, after))]

    def run_status(self,sid,rid):
        with self.connect() as db:
            return self._get(db,sid,'run',rid)['status']

    def review(self, sid, rid, body):
        with self.transaction() as db:
            state, run = self._session(db, sid), self._get(db, sid, 'run', rid)
            if run['status'] != 'awaiting_review':
                raise LiveConflict('当前任务不在待确认状态')
            if body['expected_state_revision'] != state['state_revision'] or run['base_state_revision'] != state['state_revision'] or run['configuration_id'] != state['configuration_id']:
                raise LiveConflict('确认基础版本过期，旧提案已保留，请重新准备复核')
            if body['decision'] == 'reject' and not body['reason'].strip():
                raise InputError('打回必须填写原因')
            if body['decision'] == 'adopt':
                old = state['projection']
                proposal = copy.deepcopy(run['proposal'])
                # Reuse stable IDs for unchanged/unique extensions. Splits/merges
                # get new IDs and parent lineage; retired IDs are never recycled.
                old_groups = {g['group_ref']: g for g in old.get('stream_groups', [])}
                new_groups = {g['group_ref']: g for g in proposal['stream_groups']}
                def support_of(item, key, group_lookup):
                    if key == 'stream_groups':
                        return set(item['observation_ids'])
                    return {oid for ref in item['stream_group_refs'] for oid in group_lookup[ref]['observation_ids']}
                for key, members in (('stream_groups', 'observation_ids'), ('target_hypotheses', 'stream_group_refs')):
                    for item in proposal[key]:
                        support = support_of(item, key, new_groups)
                        parents = [x for x in old.get(key, []) if support_of(x, key, old_groups) & support]
                        identical = next((x for x in parents if support_of(x, key, old_groups) == support), None)
                        unique_parent = parents[0] if len(parents) == 1 else None
                        extended = unique_parent and support_of(unique_parent, key, old_groups) <= support and sum(bool(support_of(other, key, new_groups) & support_of(unique_parent, key, old_groups)) for other in proposal[key]) == 1
                        reusable = identical or (unique_parent if extended else None)
                        item['stable_id'] = reusable['stable_id'] if reusable else uid('group' if key == 'stream_groups' else 'target')
                        item['parent_ids'] = [x['stable_id'] for x in parents]
                        item['transition'] = 'unchanged' if identical else 'extension' if extended else 'merge' if len(parents)>1 else 'split_or_revised' if parents else 'new'
                        item['status'] = 'review_adopted'
                proposal.update(scope_ids=run['scope_ids'], snapshot_id=run['snapshot_id'],
                                event_range=run['event_range'], revision=state['state_revision']+1)
                state['receipt_seq'] += 1
                proposal['knowledge_seq'] = state['receipt_seq']
                db.execute('INSERT INTO receipts VALUES(?,?,?)', (sid, state['receipt_seq'], dump({'receipt_seq': state['receipt_seq'], 'received_at': now(), 'status': 'review', 'run_id': rid})))
                # A partial revision cannot erase earlier adopted support implicitly.
                covered = set(run['scope_ids'])
                if set(old.get('scope_ids', []))-covered:
                    raise LiveConflict('当前快照未覆盖已有投影；请准备包含历史的完整复核，不静默撤回旧归属')
                proposal['superseded_ids'] = [x['stable_id'] for k in ('stream_groups', 'target_hypotheses') for x in old.get(k, []) if x['stable_id'] not in {y['stable_id'] for y in proposal[k]}]
                state['projection'] = proposal
            state['state_revision'] += 1
            run.update(status='completed' if body['decision'] == 'adopt' else 'rejected', review={**body, 'at': now()})
            self._put(db, sid, 'projection', uid('revision'), {'state_revision': state['state_revision'], 'projection': state['projection'], 'decision': body, 'run_id': rid})
            self._save(db, state)
            self._put(db, sid, 'run', rid, run)
            self._event(db, rid, {'event': 'status', 'status': run['status']})
            self._invalidate_proposals(db,sid,except_id=rid)
            return run

    def publish_prediction(self, sid, rid, value):
        with self.transaction() as db:
            state, run = self._session(db, sid), self._get(db, sid, 'run', rid)
            snapshot = self._get(db, sid, 'snapshot', run['snapshot_id'])
            stale = state['state_revision'] != run['base_state_revision'] or state['configuration_id'] != run['configuration_id']
            prediction = {**value, 'prediction_id': uid('prediction'), 'snapshot_id': snapshot['snapshot_id'],
                          'issued_at': now(), 'knowledge_cutoff_seq': snapshot['knowledge_cutoff_seq'],
                          'forecast_origin_time': snapshot['forecast_origin_time'], 'purpose': snapshot['purpose'],
                          'scope_ids': snapshot['scope_ids'], 'configuration_id': snapshot.get('evidence_configuration_id', snapshot['configuration_id']),
                          'parent_prediction_id': snapshot.get('parent_prediction_id'),
                          'revision_reason': snapshot['replay_plan'], 'status': 'stale' if stale else 'published'}
            self._put(db, sid, 'prediction', prediction['prediction_id'], prediction)
            run.update(status='stale' if stale else 'completed', prediction_id=prediction['prediction_id'])
            self._put(db, sid, 'run', rid, run)
            self._event(db, rid, {'event': 'status', 'status': run['status']})
            return prediction

    def retract(self, sid, expected, ids, reason):
        if not reason.strip():
            raise InputError('撤回必须说明原因')
        with self.transaction() as db:
            state = self._session(db, sid)
            if expected != state['state_revision']:
                raise LiveConflict('撤回基础版本过期')
            old = state['projection']
            known = {x['stable_id'] for k in ('stream_groups','target_hypotheses') for x in old.get(k, [])}
            if len(ids) != len(set(ids)) or not set(ids) <= known:
                raise InputError('撤回编号未知或重复')
            projection = copy.deepcopy(old)
            removed = [g for g in old.get('stream_groups',[]) if g['stable_id'] in ids]
            refs = {g['group_ref'] for g in removed}
            projection['stream_groups'] = [g for g in old.get('stream_groups',[]) if g['stable_id'] not in ids]
            projection['target_hypotheses'] = [t for t in old.get('target_hypotheses',[]) if t['stable_id'] not in ids and not set(t['stream_group_refs']) & refs]
            for group in removed:
                projection['unresolved_groups'].append({'observation_ids':group['observation_ids'], 'reason_code':'insufficient', 'possible_group_refs':[], 'status':'waiting_evidence'})
            for unresolved in projection.get('unresolved_groups',[]):
                unresolved['possible_group_refs'] = [ref for ref in unresolved.get('possible_group_refs',[]) if ref not in refs]
                unresolved['status'] = 'ambiguous' if unresolved['possible_group_refs'] else 'waiting_evidence'
            retained = {x['stable_id'] for k in ('stream_groups','target_hypotheses') for x in projection[k]}
            projection['superseded_ids'] = sorted(known-retained)
            state['state_revision'] += 1; state['receipt_seq'] += 1
            projection.update(revision=state['state_revision'], knowledge_seq=state['receipt_seq'])
            projection['quality'] = {'assigned':sum(len(g['observation_ids']) for g in projection['stream_groups']),
                                     'waiting_evidence':sum(len(g['observation_ids']) for g in projection['unresolved_groups'] if not g['possible_group_refs']),
                                     'ambiguous':sum(len(g['observation_ids']) for g in projection['unresolved_groups'] if g['possible_group_refs'])}
            state['projection'] = projection
            self._put(db, sid, 'projection', uid('revision'), {'state_revision':state['state_revision'],'projection':projection,'decision':{'decision':'retract','stable_ids':ids,'reason':reason}})
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (sid,state['receipt_seq'],dump({'receipt_seq':state['receipt_seq'],'received_at':now(),'status':'review','decision':'retract'})))
            self._save(db,state)
            self._invalidate_proposals(db,sid)
            return state

    def archive_legacy(self, sid, history):
        # Historical results remain labelled archive, never adopted v2 evidence.
        allowed = {'rounds','prediction_ledger','tracks','targets','session_id','version'}
        archive = audit_value({k:history[k] for k in allowed if k in history})
        with self.transaction() as db:
            self._session(db,sid)
            self._put(db,sid,'legacy_archive',uid('legacy'),{'history':archive,'evaluation_or_archive_only':True,
                'original_unchanged':True,'measurement_mapping':{p['source_observation_id']:p['observation_id'] for p in self.observations(sid)}})

    def recover_interrupted(self):
        # Called once at application startup, never by read-only status requests.
        with self.transaction() as db:
            rows = db.execute("SELECT sid,id,body FROM entities WHERE kind='run'").fetchall()
            for row in rows:
                run = json.loads(row['body'])
                if run['status'] in ('authorized', 'dispatching', 'running'):
                    not_sent = run['status'] == 'authorized'
                    run.update(status='failed' if not_sent else 'unknown', error_type='InterruptedBeforeSend' if not_sent else 'ProcessInterrupted',
                               error='尚未进入发送窗口，进程已停止；未发送，需重新准备并授权。' if not_sent else '进程已停止，无法确认上游结果；未自动重新发送，须核对费用。')
                    self._put(db, row['sid'], 'run', row['id'], run)
                    self._event(db, row['id'], {'event': 'status', 'status': run['status']})

    def export(self, sid):
        with self.connect() as db:
            result = {'format': 'live-ledger-export-v1', 'state': self._session(db, sid),
                    'observations': [json.loads(r[0]) for r in db.execute('SELECT body FROM observations WHERE sid=? ORDER BY receipt_seq', (sid,))],
                    'receipts': [json.loads(r[0]) for r in db.execute('SELECT body FROM receipts WHERE sid=? ORDER BY seq', (sid,))],
                    'versions': {kind: self._list(db, sid, kind) for kind in ('configuration', 'snapshot', 'run', 'projection', 'prediction','legacy_archive')}}
            result['versions']['run'] = [self._public_run(db,r) for r in result['versions']['run']]
            result['run_events'] = {r['run_id']:[json.loads(row[0]) for row in db.execute('SELECT body FROM run_events WHERE run_id=? ORDER BY seq',(r['run_id'],))] for r in result['versions']['run']}
            return result
