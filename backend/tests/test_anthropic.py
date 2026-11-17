import asyncio
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.app.infrastructure.llm import client
from backend.app.infrastructure.llm import stream_client
from backend.app.core.config import model_settings,model_request_options
from backend.tests.test_client import PNG


def event(kind,**data):
    return ('event: '+kind+'\ndata: '+json.dumps({'type':kind,**data},ensure_ascii=False)+'\n\n').encode()


class AnthropicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env=patch.dict(os.environ,{'MODEL_API_FORMAT':'anthropic','MODEL_AUTH_MODE':'bearer','MODEL_MAX_TOKENS':'8192','MODEL_REASONING_EFFORT':'low','MODEL_THINKING_MODE':'auto'})
        env.start();self.addCleanup(env.stop)
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.image=self.root/'hull.png';self.image.write_bytes(PNG)
        self.real_client=httpx.AsyncClient

    def test_endpoint_normalization_and_rejection(self):
        for url in ('https://www.dmxapi.cn','https://www.dmxapi.cn/v1','https://www.dmxapi.cn/v1/messages'):
            self.assertEqual(client.endpoint(url,'anthropic'),'https://www.dmxapi.cn/v1/messages')
        self.assertEqual(client.endpoint('https://api.anthropic.com','anthropic'),'https://api.anthropic.com/v1/messages')
        for url in ('https://www.dmxapi.cn/v1/chat/completions','https://evil.test/v1','http://api.anthropic.com','https://api.anthropic.com.evil.test/v1','https://api.anthropic.com/v1?key=secret'):
            with self.assertRaises(client.ClientError):client.endpoint(url,'anthropic')

    def test_anthropic_body_images_system_and_no_openai_parameters(self):
        body,metadata=client.build_request([self.image],'识别', 'qwen3.8-omni-flash',api_format='anthropic')
        self.assertIn('简体中文',body['system'])
        self.assertEqual([x['role'] for x in body['messages']],['user'])
        source=body['messages'][0]['content'][1]['source']
        self.assertEqual(base64.b64decode(source['data']),PNG)
        self.assertEqual(source['media_type'],'image/png')
        self.assertEqual(body['max_tokens'],8192)
        self.assertNotIn('reasoning_effort',body);self.assertNotIn('thinking',body)
        self.assertEqual(model_request_options('anthropic'),{})
        self.assertEqual(len(metadata),1)
        body,_=client.build_request([],'点位','model',allow_text_only=True,api_format='anthropic')
        self.assertEqual(body['messages'][0]['content'],[{'type':'text','text':'点位'}])

    def test_auth_modes_and_environment_fallback(self):
        headers=client.request_headers('fake-key','anthropic')
        self.assertEqual(headers['Authorization'],'Bearer fake-key')
        self.assertEqual(headers['anthropic-version'],'2023-06-01')
        self.assertNotIn('x-api-key',headers)
        with patch.dict(os.environ,{'MODEL_AUTH_MODE':'x-api-key'}):
            headers=client.request_headers('fake-key','anthropic')
            self.assertEqual(headers['x-api-key'],'fake-key');self.assertNotIn('Authorization',headers)
        with patch.dict(os.environ,{'ANTHROPIC_BASE_URL':'https://www.dmxapi.cn','ANTHROPIC_AUTH_TOKEN':'fake-token','ANTHROPIC_MODEL':'same-cli-model'},clear=True):
            settings=model_settings()
            self.assertEqual(settings.api_format,'anthropic');self.assertEqual(settings.api_key,'fake-token')
            self.assertEqual(settings.model,'same-cli-model')
            self.assertEqual(client.request_headers(settings.api_key,settings.api_format)['Authorization'],'Bearer fake-token')
            with patch.dict(os.environ,{'MODEL_API_KEY':''}):self.assertEqual(model_settings().api_key,'')

    def test_explicit_thinking_switch(self):
        with patch.dict(os.environ,{'MODEL_THINKING_MODE':'disabled'}):
            body,_=client.build_request([],'points','model',allow_text_only=True,api_format='anthropic')
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertNotIn('reasoning_effort',body)
        with patch.dict(os.environ,{'MODEL_THINKING_MODE':'bad'}):
            with self.assertRaises(ValueError):model_request_options('anthropic')

    async def test_reasoning_exhausted_preserves_diagnostics_no_retry(self):
        raw=(event('content_block_delta',index=0,delta={'type':'thinking_delta','thinking':'not final'})+
             event('message_delta',delta={'stop_reason':'max_tokens'},usage={'output_tokens':8192})+
             event('message_stop'))
        calls=[]
        async def handle(request):
            calls.append(1)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=raw)
        with self.transport(handle):
            with self.assertRaisesRegex(client.ClientError,'额度.*max_tokens'):
                async for _ in self.call():pass
        self.assertEqual(len(calls),1)
        run=next((self.root/'outputs').iterdir())
        state=json.loads((run/'stream_state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['provider_stop_reason'],'max_tokens')
        self.assertEqual(state['usage']['output_tokens'],8192)
        self.assertFalse(state['has_body'])
        self.assertEqual((run/'provider_reasoning.txt').read_text(encoding='utf-8'),'not final')

    def test_sync_response_content_blocks(self):
        reply={'type':'message','id':'msg_test','content':[{'type':'thinking','thinking':'not final'}, {'type':'text','text':'轨迹JSON'}],'usage':{'input_tokens':3,'output_tokens':4}}
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Opener:
            def open(inner,request,timeout):
                self.assertEqual(request.full_url,'https://www.dmxapi.cn/v1/messages')
                self.assertEqual(request.get_header('Anthropic-version'),'2023-06-01')
                self.assertEqual(json.loads(request.data)['messages'][0]['role'],'user')
                return Reply(json.dumps(reply).encode())
        with patch('backend.app.infrastructure.llm.client.urllib.request.build_opener',return_value=Opener()):
            result=client.send_images([],'点位','same-cli-model','https://www.dmxapi.cn/v1',api_key='fake-key',output_dir=self.root/'sync',allow_text_only=True)
        self.assertEqual(result['answer'],'轨迹JSON')

    def transport(self,handler):
        return patch('backend.app.infrastructure.llm.stream_client.httpx.AsyncClient',side_effect=lambda **kw:self.real_client(transport=httpx.MockTransport(handler),**kw))

    def call(self):
        return stream_client.stream_images([],'validated points','qwen3.8-omni-flash','https://www.dmxapi.cn/v1','fake-key',self.root/'outputs',allow_text_only=True)

    async def test_real_anthropic_sse_shape_incremental_and_usage(self):
        release=asyncio.Event()
        class Body(httpx.AsyncByteStream):
            async def __aiter__(inner):
                yield event('message_start',message={'id':'msg_1','usage':{'input_tokens':17,'output_tokens':1},'content':[]})
                yield event('content_block_start',index=0,content_block={'type':'thinking','thinking':''})
                yield event('content_block_delta',index=0,delta={'type':'thinking_delta','thinking':'服务推理'})
                yield event('content_block_delta',index=0,delta={'type':'signature_delta','signature':'not-text'})
                yield event('content_block_start',index=1,content_block={'type':'text','text':''})
                raw=event('content_block_delta',index=1,delta={'type':'text_delta','text':'正文'})
                for i in range(0,len(raw),3):yield raw[i:i+3]
                await release.wait()
                yield event('message_delta',delta={'stop_reason':'end_turn'},usage={'output_tokens':9})
                yield event('message_stop')
        async def handle(request):
            self.assertEqual(request.url.path,'/v1/messages')
            self.assertEqual(request.headers['anthropic-version'],'2023-06-01')
            self.assertEqual(request.headers['authorization'],'Bearer fake-key')
            body=json.loads(await request.aread());self.assertTrue(body['stream'])
            self.assertEqual(body['model'],'qwen3.8-omni-flash');self.assertNotIn('reasoning_effort',body)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
        with self.transport(handle):
            stream=self.call();received=[]
            async with asyncio.timeout(5):
                while True:
                    item=await anext(stream);received.append(item)
                    if item['event']=='delta':break
            self.assertFalse(release.is_set());release.set()
            received += [e async for e in stream]
        final=next(e['result'] for e in received if e['event']=='model_complete')
        self.assertEqual(final['answer'],'正文');self.assertEqual(final['finish_reason'],'stop')
        self.assertEqual(final['usage'],{'input_tokens':17,'output_tokens':9})
        self.assertTrue(final['has_reasoning'])
        metadata=json.loads((Path(final['output_dir'])/'request_meta.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['api_format'],'anthropic');self.assertNotIn('fake-key',json.dumps(metadata))

    async def test_anthropic_error_and_incomplete_stream_no_retry(self):
        for raw in (event('error',error={'type':'permission_error','message':'fake-key denied'}),
                    event('content_block_delta',index=0,delta={'type':'text_delta','text':'partial'})+event('message_delta',delta={'stop_reason':'end_turn'})):
            calls=[]
            async def handle(request):
                calls.append(1)
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content=raw)
            with self.transport(handle):
                with self.assertRaises(client.ClientError) as caught:
                    async for _ in self.call():pass
            self.assertEqual(len(calls),1);self.assertNotIn('fake-key',str(caught.exception))


if __name__=='__main__':unittest.main()
