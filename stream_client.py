"""Actual upstream SSE streaming; no retries, no local timeout, no code execution."""
from datetime import datetime,timezone
import asyncio
import json
from pathlib import Path
import re
import uuid

import httpx

from client import ClientError,answer_text,build_request,endpoint,redact,request_headers
from config import model_api_format


class DeltaRedactor:
    """Retain a possible credential prefix between chunks so it cannot leak."""
    def __init__(self,key):self.key=key;self.pending=''

    def feed(self,text='',final=False):
        self.pending+=text
        if self.key:self.pending=self.pending.replace(self.key,'[REDACTED]')
        hold=0
        if self.key and not final:
            for n in range(1,min(len(self.pending),len(self.key)-1)+1):
                if self.pending.endswith(self.key[:n]):hold=n
        output=self.pending[:-hold] if hold else self.pending
        self.pending=self.pending[-hold:] if hold else ''
        return output


def safe_error(data,key):
    error=data.get('error',data) if isinstance(data,dict) else {}
    if not isinstance(error,dict):return '服务返回错误，未输出原始响应体'
    code=str(error.get('code',error.get('type','')))
    code=code if re.fullmatch(r'[\w.\-]{1,80}',code) else ''
    message=redact(str(error.get('message','模型服务返回错误')),key)
    message=re.sub(r'data:image/\S+','[IMAGE OMITTED]',message)
    message=re.sub(r'(?i)Bearer\s+\S+|sk-[A-Za-z0-9_\-]{8,}','[REDACTED]',message)
    return (code+'：' if code else '')+message.replace('\r',' ').replace('\n',' ')[:500]


async def stream_images(images,prompt,model,base_url,api_key,output_dir,*,allow_text_only=False,api_format=None):
    if not api_key or '\n' in api_key or '\r' in api_key:raise ClientError('请检查服务端MODEL_API_KEY')
    api_format=api_format or model_api_format()
    url=endpoint(base_url,api_format)
    body,metadata=build_request(images,prompt,model,allow_text_only=allow_text_only,api_format=api_format)
    body['stream']=True
    run=Path(output_dir)/(datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8])
    run.mkdir(parents=True,exist_ok=False)
    content=[];reasoning=[];usage=None;response_id=None;finish_reason=None;done=False;total=0;provider_stop_reason=None
    guards={'delta':DeltaRedactor(api_key),'reasoning':DeltaRedactor(api_key)}
    def save(name,value):
        path=run/name
        path.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    def text_value(value):
        if isinstance(value,str):return value
        if isinstance(value,list):return ''.join(x.get('text','') for x in value if isinstance(x,dict) and isinstance(x.get('text'),str))
        return ''
    def consume(data):
        nonlocal usage,response_id,finish_reason,done,provider_stop_reason
        if not isinstance(data,dict):raise ClientError('流式数据不是JSON对象')
        if data.get('error'):raise ClientError(safe_error(data,api_key))
        if api_format=='anthropic':
            kind=data.get('type');events=[]
            pieces=[]
            if kind=='message_start':
                message=data.get('message') or {}
                response_id=redact(message.get('id'),api_key)
                if isinstance(message.get('usage'),dict):usage=redact(message['usage'],api_key)
                pieces=message.get('content') or []
            elif kind=='content_block_start':pieces=[data.get('content_block') or {}]
            elif kind=='content_block_delta':pieces=[data.get('delta') or {}]
            elif kind=='message_delta':
                delta=data.get('delta') or {}
                if delta.get('stop_reason') is not None:
                    provider_stop_reason=str(delta['stop_reason'])
                    finish_reason='stop' if provider_stop_reason in ('end_turn','stop_sequence') else provider_stop_reason
                if isinstance(data.get('usage'),dict):usage={**(usage or {}),**redact(data['usage'],api_key)}
            elif kind=='message_stop':done=True
            for piece in pieces:
                name=piece.get('type')
                event_kind='delta' if name in ('text','text_delta') else 'reasoning' if name in ('thinking','thinking_delta') else None
                if event_kind:
                    value=piece.get('text') if event_kind=='delta' else piece.get('thinking')
                    text=guards[event_kind].feed(text_value(value))
                    if text:
                        (content if event_kind=='delta' else reasoning).append(text)
                        events.append({'event':event_kind,'text':text})
            return events
        if data.get('id'):response_id=redact(str(data['id']),api_key)
        if isinstance(data.get('usage'),dict):usage=redact(data['usage'],api_key)
        events=[]
        for choice in data.get('choices',[]):
            if choice.get('index',0)!=0:continue
            delta=choice.get('delta') or {}
            if choice.get('finish_reason') is not None:finish_reason=str(choice['finish_reason'])
            for kind,value in [('delta',delta.get('content')),('reasoning',delta.get('reasoning_content'))]:
                text=guards[kind].feed(text_value(value))
                if text:
                    (content if kind=='delta' else reasoning).append(text)
                    events.append({'event':kind,'text':text})
        return events
    save('request_meta.json',{'created_at':datetime.now(timezone.utc).isoformat(),'endpoint':url,
        'model':model,'api_format':api_format,'prompt':redact(prompt,api_key),'images':metadata,'stream':True,
        'reasoning_effort':body.get('reasoning_effort')})
    yield {'event':'phase','phase':'connecting','message':f'正在连接模型服务（{api_format}，{model}），等待HTTP响应头；本地不设等待时限。'}
    try:
        async with httpx.AsyncClient(timeout=None,follow_redirects=False) as client:
            async with client.stream('POST',url,json=body,headers=request_headers(api_key,api_format,'text/event-stream')) as reply:
                yield {'event':'phase','phase':'headers','message':f'已收到上游HTTP {reply.status_code}响应头。'}
                if reply.status_code!=200:
                    raw=b''
                    async for piece in reply.aiter_bytes():
                        raw+=piece
                        if len(raw)>=65536:break
                    try:detail=safe_error(json.loads(raw[:65536]),api_key)
                    except (ValueError,UnicodeDecodeError):detail='上游未返回可解析的JSON错误，原始正文未展示'
                    raise ClientError(f'上游HTTP {reply.status_code}（{api_format}）：{detail}。请核对协议、入口、密钥与模型权限；未自动重试。')
                ctype=reply.headers.get('content-type','').lower()
                if 'text/event-stream' not in ctype:
                    if 'application/json' not in ctype:raise ClientError('上游未返回SSE或JSON，请检查接口/中转站路由')
                    yield {'event':'phase','phase':'buffered','message':'中转站返回整包JSON而非SSE，只能收到完整回复后展示，非实时输出。'}
                    raw=b''
                    async for piece in reply.aiter_bytes():
                        raw+=piece
                        if len(raw)>16*1024*1024:raise ClientError('上游响应超过16MiB')
                    data=json.loads(raw)
                    if data.get('error'):raise ClientError(safe_error(data,api_key))
                    text=guards['delta'].feed(answer_text(data),final=True)
                    if text:content.append(text);yield {'event':'delta','text':text}
                    usage=redact(data.get('usage'),api_key);response_id=redact(data.get('id'),api_key)
                    done=True;finish_reason='buffered_response'
                else:
                    yield {'event':'phase','phase':'connected','message':'SSE连接已建立，等待模型首个输出片段。'}
                    lines=[];event_size=0
                    async for line in reply.aiter_lines():
                        size=len(line.encode('utf-8'));total+=size;event_size+=size
                        if total>16*1024*1024 or event_size>1024*1024:raise ClientError('上游流式响应或单事件超过大小限制')
                        if line.startswith('data:'):lines.append(line[5:].lstrip())
                        if line=='':
                            if lines:
                                payload='\n'.join(lines);lines=[]
                                if payload.strip()=='[DONE]':
                                    if api_format=='anthropic':raise ClientError('Anthropic接口返回OpenAI结束标记，请检查中转站协议路由')
                                    done=True;break
                                for event in consume(json.loads(payload)):yield event
                                if done:break
                            event_size=0
                    if lines and not done:
                        payload='\n'.join(lines)
                        if payload.strip()=='[DONE]' and api_format=='openai':done=True
                        else:
                            for event in consume(json.loads(payload)):yield event
                    for kind,guard in guards.items():
                        text=guard.feed(final=True)
                        if text:
                            (content if kind=='delta' else reasoning).append(text)
                            yield {'event':kind,'text':text}
                    if not done and (api_format=='anthropic' or finish_reason is None):raise ClientError('上游流提前断开，未收到结束标记；已显示内容可能不完整。')
        answer=''.join(content)
        if not answer:raise ClientError('上游流已结束，但没有返回正文；请检查是否只有推理文本或模型拒绝输出。')
        response={'id':response_id,'model':model,'choices':[{'index':0,'finish_reason':finish_reason,
            'message':{'role':'assistant','content':answer}}],'usage':usage,'stream_assembled':True}
        if api_format=='anthropic':
            response={'id':response_id,'type':'message','role':'assistant','model':model,
                'content':[{'type':'text','text':answer}],'stop_reason':provider_stop_reason,'usage':usage,'stream_assembled':True}
        save('answer.txt',answer);save('response.json',response)
        if reasoning:save('provider_reasoning.txt',''.join(reasoning))
        save('stream_state.json',{'state':'completed','finish_reason':finish_reason,'provider_stop_reason':provider_stop_reason,'api_format':api_format,'response_id':response_id})
        yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(run.resolve()),'usage':usage,
            'finish_reason':finish_reason,'has_reasoning':bool(reasoning)}}
    except asyncio.CancelledError:
        save('answer.partial.txt',''.join(content))
        save('stream_state.json',{'state':'cancelled_locally','partial':True})
        raise
    except (httpx.HTTPError,ValueError,OSError,ClientError) as exc:
        save('answer.partial.txt',''.join(content))
        save('stream_state.json',{'state':'failed','partial':True,'reason':type(exc).__name__})
        if isinstance(exc,ClientError):raise
        if isinstance(exc,(json.JSONDecodeError,UnicodeDecodeError)):raise ClientError('中转站返回了无法解析的流式JSON，已显示内容可能不完整。') from None
        raise ClientError('模型流连接或本地保存失败（'+type(exc).__name__+'）；不自动重试，请核对中转站调用记录。') from None
