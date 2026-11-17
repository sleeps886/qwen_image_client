import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from backend.app.infrastructure.llm import stream_client
from backend.app.infrastructure.llm.client import ClientError
from backend.tests.test_client import PNG


class StreamingClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        protocol=patch.dict(os.environ,{'MODEL_API_FORMAT':'openai','MODEL_AUTH_MODE':'bearer'})
        protocol.start();self.addCleanup(protocol.stop)
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.image=self.root/'hull.png';self.image.write_bytes(PNG)
        self.real_client=httpx.AsyncClient

    def mock_client(self,response):
        async def handle(request):
            body=json.loads(await request.aread())
            self.assertTrue(body['stream'])
            self.assertEqual(request.headers['authorization'],'Bearer fake-test-key')
            return response
        transport=httpx.MockTransport(handle)
        return patch('backend.app.infrastructure.llm.stream_client.httpx.AsyncClient',side_effect=lambda **kw:self.real_client(transport=transport,**kw))

    def call(self):
        return stream_client.stream_images([self.image],'test','vision-model','https://www.dmxapi.cn/v1','fake-test-key',self.root/'outputs')

    async def test_text_only_stream_request_has_no_image_parts(self):
        async def handle(request):
            body=json.loads(await request.aread())
            self.assertTrue(body['stream'])
            self.assertTrue(all(part['type']=='text' for part in body['messages'][1]['content']))
            return httpx.Response(200,json={'choices':[{'message':{'content':'文件轨迹JSON'}}]})
        transport=httpx.MockTransport(handle)
        with patch('backend.app.infrastructure.llm.stream_client.httpx.AsyncClient',side_effect=lambda **kw:self.real_client(transport=transport,**kw)):
            events=[e async for e in stream_client.stream_images([], 'validated data', 'model',
                'https://www.dmxapi.cn/v1', 'fake-test-key', self.root/'outputs',allow_text_only=True)]
        self.assertTrue(any(e['event']=='model_complete' for e in events))

    async def test_first_delta_before_upstream_finishes(self):
        release=asyncio.Event()
        def chunk(text,finish=None):
            return ('data: '+json.dumps({'choices':[{'index':0,'delta':{'content':text},'finish_reason':finish}]},ensure_ascii=False)+'\n\n').encode()
        class Body(httpx.AsyncByteStream):
            async def __aiter__(inner):
                first=chunk('你好')
                # Split even inside UTF-8/multipart transport boundaries.
                for i in range(0,len(first),3):yield first[i:i+3]
                await release.wait()
                yield chunk('世界','stop')
                yield b'data: [DONE]\n\n'
        response=httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body())
        with self.mock_client(response):
            stream=self.call()
            async with asyncio.timeout(5):
                while True:
                    event=await anext(stream)
                    if event['event']=='delta':break
            self.assertEqual(event['text'],'你好')
            self.assertFalse(release.is_set())
            release.set()
            rest=[e async for e in stream]
        final=next(e['result'] for e in rest if e['event']=='model_complete')
        self.assertEqual(final['answer'],'你好世界')
        self.assertEqual((Path(final['output_dir'])/'answer.txt').read_text(encoding='utf-8'),'你好世界')

    async def test_partial_stream_never_marked_complete(self):
        raw=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        response=httpx.Response(200,headers={'content-type':'text/event-stream'},content=raw)
        events=[]
        with self.mock_client(response):
            with self.assertRaises(ClientError):
                async for event in self.call():events.append(event)
        self.assertFalse(any(e['event']=='model_complete' for e in events))
        self.assertEqual(next((self.root/'outputs').glob('*/stream_state.json')).read_text(encoding='utf-8').count('failed'),1)

    async def test_buffered_fallback_and_error_redaction(self):
        response=httpx.Response(200,json={'choices':[{'message':{'content':'完整回复'}}]})
        with self.mock_client(response):events=[e async for e in self.call()]
        self.assertTrue(any(e.get('phase')=='buffered' for e in events))
        response=httpx.Response(403,json={'error':{'code':'denied','message':'fake-test-key is not permitted'}})
        with self.mock_client(response):
            with self.assertRaises(ClientError) as error:
                async for _ in self.call():pass
        self.assertNotIn('fake-test-key',str(error.exception))
        self.assertIn('denied',str(error.exception))

    def test_redact_across_delta_boundaries(self):
        guard=stream_client.DeltaRedactor('fake-test-key')
        output=guard.feed('hello fake-')+guard.feed('test-key world')+guard.feed(final=True)
        self.assertEqual(output,'hello [REDACTED] world')
