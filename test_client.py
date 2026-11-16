import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import client

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhVEAAAAASUVORK5CYII=')


class ClientTests(unittest.TestCase):
    def setUp(self):
        protocol=patch.dict(os.environ,{'MODEL_API_FORMAT':'openai','MODEL_AUTH_MODE':'bearer'})
        protocol.start();self.addCleanup(protocol.stop)
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.image=self.root/'btr_A_hull.png';self.image.write_bytes(PNG)

    def test_request_images_and_endpoint(self):
        body,meta=client.build_request([self.image],'识别亮带','qwen3-vl-plus')
        encoded=next(m for m in body['messages'] if m['role']=='user')['content'][1]['image_url']['url']
        self.assertEqual(body['messages'][0]['role'],'system')
        self.assertIn('简体中文',body['messages'][0]['content'])
        self.assertEqual(base64.b64decode(encoded.split(',')[1]),PNG)
        self.assertEqual(meta[0]['bytes'],len(PNG))
        self.assertFalse(body['stream'])
        self.assertEqual(client.endpoint(client.DEFAULT_BASE_URL),client.DEFAULT_BASE_URL+'/chat/completions')
        self.assertIn('cn-beijing',client.endpoint('https://workspace123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'))
        self.assertEqual(client.endpoint('https://www.dmxapi.cn/v1'),'https://www.dmxapi.cn/v1/chat/completions')
        self.assertEqual(client.endpoint('https://dmxapi.cn/v1/chat/completions'),'https://dmxapi.cn/v1/chat/completions')
        for url in ('https://www.dmxapi.cn.evil.test/v1','http://www.dmxapi.cn/v1','https://www.dmxapi.cn/compatible-mode/v1'):
            with self.assertRaises(client.ClientError):client.endpoint(url)
        for url in ('http://dashscope.aliyuncs.com/compatible-mode/v1',
                    'https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1',
                    'https://example.com/compatible-mode/v1',
                    'https://secret@dashscope.aliyuncs.com/compatible-mode/v1'):
            with self.assertRaises(client.ClientError):client.endpoint(url)
        self.image.write_text('not an image')
        with self.assertRaises(client.ClientError):client.build_request([self.image],'x','y')

    def test_reasoning_effort_in_request(self):
        with patch('client.model_request_options',return_value={'reasoning_effort':'low'}):
            body,_=client.build_request([self.image],'仅返回中文说明的JSON','glm-5.3-flash')
        self.assertEqual(body['reasoning_effort'],'low')
        self.assertNotIn('thinking',body)

    def test_chinese_instruction_even_with_custom_prompt(self):
        body,_=client.build_request([self.image],'Analyze these images.','test-model')
        self.assertIn('自然语言',body['messages'][0]['content'])
        self.assertIn('简体中文',body['messages'][0]['content'])
        self.assertEqual(body['messages'][-1]['content'][-1]['text'],'Analyze these images.')

    def test_only_sample_input(self):
        input_dir=self.root/'input';input_dir.mkdir()
        (input_dir/'btr_A_hull.png').write_bytes(PNG)
        (input_dir/'btr_A_tow.png').write_bytes(PNG)
        truth=self.root/'truth';truth.mkdir();(truth/'btr_A_track.png').write_bytes(PNG)
        self.assertEqual(len(client.sample_images(self.root)),2)
        self.assertTrue(all(p.parent.name=='input' for p in client.sample_images(self.root)))

    def test_success_save_and_no_secrets(self):
        secret='fake-test-key-not-a-real-key'
        response=dict(id='response-id',choices=[dict(message=dict(content='目标约2个。 '+secret))],usage=dict(total_tokens=10))
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Opener:
            def open(inner,request,timeout):
                self.assertEqual(request.get_header('Authorization'),'Bearer '+secret)
                self.assertIsNone(timeout)
                self.assertEqual(json.loads(request.data)['model'],client.DEFAULT_MODEL)
                return Reply(json.dumps(response).encode())
        with patch('client.urllib.request.build_opener',return_value=Opener()):
            result=client.send_images([self.image],api_key=secret,output_dir=self.root/'results')
        self.assertIn('[REDACTED]',result['answer'])
        files=list(Path(result['output_dir']).iterdir())
        self.assertEqual(len(files),3)
        for path in files:self.assertNotIn(secret,path.read_text(encoding='utf-8'))
        metadata=json.loads((Path(result['output_dir'])/'request_meta.json').read_text(encoding='utf-8'))
        self.assertNotIn('messages',metadata)
        self.assertEqual(metadata['usage']['total_tokens'],10)

    def test_failure_no_retry_or_error_body_leak(self):
        error=urllib.error.HTTPError(client.DEFAULT_BASE_URL,401,'bad',{},io.BytesIO(b'private body'))
        with patch('client.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaises(client.ClientError) as caught:
                client.send_images([self.image],api_key='fake-key',output_dir=self.root/'results')
            self.assertIn('401',str(caught.exception))
            self.assertNotIn('private body',str(caught.exception))
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertIsNone(client.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))

    def test_timeout_distinguished_without_retry(self):
        with patch('client.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError()
            with self.assertRaises(client.UpstreamTimeout) as caught:
                client.send_images([self.image],api_key='fake-key',output_dir=self.root/'timeouts',timeout=300)
            self.assertIn('300',str(caught.exception))
            self.assertEqual(opener.return_value.open.call_count,1)


if __name__=='__main__':unittest.main()
