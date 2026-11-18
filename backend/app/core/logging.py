"""Bounded, per-request runtime logs. Never store request/response bodies."""
from collections import OrderedDict
from datetime import datetime
import re
import threading
import time
import uuid
import logging
import os


class RunLogs:
    def __init__(self):
        self.lock=threading.Lock()
        self.runs=OrderedDict()

    def start(self, request_id=None):
        key=str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
        with self.lock:
            if key in self.runs:raise ValueError('请求编号重复，请勿重复提交同一请求')
            if len(self.runs)>=128:
                disposable=next((k for k,v in self.runs.items() if v['done']),None)
                if disposable is None:raise ValueError('日志任务已满，请稍后重试')
                del self.runs[disposable]
            self.runs[key]={'started':time.monotonic(),'done':False,'events':[]}
        self.add(key,'INFO','后端已接收请求，正在读取上传内容。')
        return key

    def add(self,key,level,message):
        message=str(message)
        # Local numerical requests must not depend on valid cloud settings.
        # Redact all supported credential sources without parsing model config.
        for name in ('MODEL_API_KEY','DASHSCOPE_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN'):
            secret=os.environ.get(name,'').strip()
            if secret:message=message.replace(secret,'[REDACTED]')
        message=re.sub(r'sk-[A-Za-z0-9_-]{8,}','[REDACTED]',message)
        message=message.replace('\r',' ').replace('\n',' ')[:1200]
        with self.lock:
            run=self.runs.get(key)
            if not run:return
            entries=run['events']
            entries.append({'seq':entries[-1]['seq']+1 if entries else 1,
                'time':datetime.now().astimezone().isoformat(timespec='seconds'),
                'elapsed_s':round(time.monotonic()-run['started'],2),
                'level':level,'message':message})
            if len(entries)>200:del entries[:-200]

    def finish(self,key,status):
        self.add(key,'ERROR' if status>=400 else 'INFO',f'请求处理结束，HTTP {status}。')
        with self.lock:
            self.runs[key]['done']=True

    def snapshot(self,key,after=0):
        with self.lock:
            run=self.runs[key]
            return {'request_id':key,'done':run['done'],
                'events':[dict(e) for e in run['events'] if e['seq']>after]}


LOGS=RunLogs()


class QuietLogPolling(logging.Filter):
    def filter(self,record):
        args=record.args
        return not (isinstance(args,tuple) and len(args)>=5 and args[2]=='GET'
            and str(args[3]).startswith('/api/logs/') and str(args[4])=='200')


def configure_access_logging():
    logging.getLogger('uvicorn.access').addFilter(QuietLogPolling())
