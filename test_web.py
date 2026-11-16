import json
import os
from pathlib import Path
import tempfile
import unittest
import threading
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import web
from test_client import PNG


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root.joinpath('uploads').mkdir()
        self.patches=[patch.object(web,'UPLOADS',self.root/'uploads'),patch.object(web,'OUTPUTS',self.root/'outputs'),
                      patch.dict(os.environ,{'MODEL_API_KEY':'fake-server-key','MODEL_NAME':'server-model',
                                            'MODEL_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'})]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.api=TestClient(web.app,base_url='http://127.0.0.1')
        self.addCleanup(self.api.close)

    def post(self,data=None,raw=PNG,name='hull.png',headers=None):
        return self.api.post('/api/recognize',data=data or {},files=[('files',(name,raw,'image/png'))],headers=headers or {})

    def test_health_page_docs(self):
        self.assertEqual(self.api.get('/api/health').status_code,200)
        self.assertIn('千问视觉接入台',self.api.get('/').text)
        self.assertEqual(self.api.get('/assets/app.js').status_code,200)
        schema=self.api.get('/openapi.json').json()
        self.assertIn('/api/recognize',schema['paths'])
        self.assertNotIn('Content-Security-Policy',self.api.get('/docs').headers)
        health=self.api.get('/api/health').json()
        for field in ('default_model','default_base_url','api_key'):
            self.assertNotIn(field,health)
        page=self.api.get('/').text
        for field in ('id="model"','id="baseUrl"','id="apiKey"'):
            self.assertNotIn(field,page)
        properties=schema['components']['schemas']
        form_schema=next(v for k,v in properties.items() if k.startswith('Body_recognize'))
        self.assertNotIn('model',form_schema['properties'])
        self.assertNotIn('base_url',form_schema['properties'])

    def test_preflight_no_cloud_and_cleanup(self):
        with patch.object(web,'send_images') as send:
            response=self.post({'dry_run':'true'},name='../../hull.png')
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['dry_run'])
            self.assertNotIn('..',response.json()['images'][0]['name'])
            send.assert_not_called()
        self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_send_and_download(self):
        rid='20261116_120000_abcdef12'
        output=self.root/'outputs'/rid;output.mkdir(parents=True)
        (output/'answer.txt').write_text('两条亮带',encoding='utf-8')
        with patch.object(web,'send_images',return_value={'answer':'两条亮带','output_dir':str(output),'usage':{'total_tokens':7}}) as send:
            response=self.post({'consent':'true'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(send.call_args.args[4],'fake-server-key')
            self.assertEqual(send.call_args.args[2],'server-model')
            data=response.json();self.assertEqual(data['result_id'],rid)
            self.assertEqual(self.api.get(data['downloads']['answer.txt']).text,'两条亮带')
        self.assertEqual(self.api.get('/api/results/bad/answer.txt').status_code,404)

    def test_reject_invalid_and_secret_validation(self):
        with patch.object(web,'send_images') as send:
            self.assertEqual(self.post().status_code,400)
            self.assertEqual(self.post({'dry_run':'true'},raw=b'not an image').status_code,400)
            response=self.post({'dry_run':'fake-secret-key'})
            self.assertEqual(response.status_code,422)
            self.assertNotIn('fake-secret-key',response.text)
            self.assertEqual(self.post({'dry_run':'true'},headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(self.post({'dry_run':'true','base_url':'https://evil.example'}).status_code,400)
            send.assert_not_called()

    def test_server_config_override_and_missing_key(self):
        with patch.object(web,'send_images') as send:
            self.assertEqual(self.post({'consent':'true','model':'other-model'}).status_code,400)
            self.assertEqual(self.post({'consent':'true'},headers={'X-Dashscope-Key':'fake-client-key'}).status_code,400)
            with patch.dict(os.environ,{'MODEL_API_KEY':''}):
                response=self.post({'consent':'true'})
                self.assertEqual(response.status_code,400)
                self.assertIn('.env',response.json()['detail'])
                self.assertEqual(self.post({'dry_run':'true'}).status_code,200)
            send.assert_not_called()

    def test_upload_limits(self):
        response=self.post({'dry_run':'true'},raw=PNG+b'x'*(web.MAX_FILE_BYTES+1))
        self.assertEqual(response.status_code,413)

    def test_upstream_timeout_is_504(self):
        with patch.object(web,'send_images',side_effect=web.UpstreamTimeout('模拟上游超时')) as send:
            response=self.post({'consent':'true'})
            self.assertEqual(response.status_code,504)
            self.assertIn('timeout',send.call_args.kwargs)
            self.assertIsNone(send.call_args.kwargs['timeout'])

    def test_structured_evaluation_is_local(self):
        from test_evaluation import prediction,truth,semantic
        rid='20261116_120000_4433ccdd';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'tracks.json').write_text(json.dumps(prediction()))
        with patch.object(web,'send_images') as send:
            response=self.api.post('/api/evaluate',data={'result_id':rid},files=[
                ('semantic',('semantic.json',semantic(),'application/json')),
                ('truth',('truth.jsonl',truth(),'text/plain'))])
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['position_metrics']['rmse_m'],0)
            self.assertEqual(self.api.get(data['report_url']).status_code,200)
            send.assert_not_called()

    def test_local_comparison_and_reject_bearing(self):
        from test_comparison import drawing
        rid='20261116_120000_9988aabb'
        root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'estimated_track.png').write_bytes(drawing('line'))
        (root/'tracks.json').write_text(json.dumps({'coordinate_system':'local_xy_m'}))
        with patch.object(web,'send_images') as send:
            response=self.api.post('/api/compare',data={'result_id':rid},files={'reference':('track.png',drawing('line'),'image/png')})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['similarity_percent'],100)
            self.assertEqual(self.api.get(data['overlay_url']).content[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(self.api.get(data['report_url']).json()['result_id'],rid)
            (root/'tracks.json').write_text(json.dumps({'coordinate_system':'bearing_time'}))
            response=self.api.post('/api/compare',data={'result_id':rid},files={'reference':('track.png',drawing('line'),'image/png')})
            self.assertEqual(response.status_code,400)
            send.assert_not_called()

    def test_request_logs_preflight_cursor_and_download(self):
        rid=str(uuid.uuid4())
        response=self.post({'dry_run':'true'},headers={'X-Request-ID':rid})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['X-Request-ID'],rid)
        data=self.api.get('/api/logs/'+rid).json()
        self.assertTrue(data['done'])
        self.assertTrue(any('本地预检完成' in x['message'] for x in data['events']))
        cursor=data['events'][-1]['seq']
        self.assertEqual(self.api.get('/api/logs/'+rid+'?after='+str(cursor)).json()['events'],[])
        text=self.api.get('/api/logs/'+rid+'/download').text
        self.assertNotIn('fake-server-key',text)
        self.assertIn('HTTP 200',text)
        self.assertEqual(self.post({'dry_run':'true'},headers={'X-Request-ID':rid}).status_code,400)

    def test_live_logs_during_model_wait(self):
        entered,release=threading.Event(),threading.Event()
        rid=str(uuid.uuid4())
        def fake(*args,**kwargs):
            entered.set();release.wait(5)
            raise web.ClientError('模拟错误 fake-server-key')
        with patch.object(web,'send_images',side_effect=fake):
            thread=threading.Thread(target=lambda:self.post({'consent':'true'},headers={'X-Request-ID':rid}))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                data=self.api.get('/api/logs/'+rid).json()
                self.assertFalse(data['done'])
                self.assertTrue(any('开始调用模型服务' in e['message'] for e in data['events']))
            finally:
                release.set();thread.join(10)
        data=self.api.get('/api/logs/'+rid).json()
        self.assertTrue(data['done'])
        self.assertNotIn('fake-server-key',json.dumps(data))
        self.assertTrue(any('[REDACTED]' in e['message'] for e in data['events']))

    def test_model_trajectory_image_and_platform(self):
        from test_trajectory import CONTEXT,result
        rid='20261116_120000_1234abcd'
        directory=self.root/'outputs'/rid;directory.mkdir(parents=True)
        reply={'answer':json.dumps(result()),'output_dir':str(directory),'usage':{}}
        with patch.object(web,'send_images',return_value=reply) as send:
            response=self.api.post('/api/recognize',data={'consent':'true'},files=[
                ('files',('hull.png',PNG,'image/png')),
                ('platform',('platform.json',json.dumps(CONTEXT).encode(),'application/json'))])
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()
            self.assertEqual(data['trajectory_status'],'position_estimate')
            self.assertEqual(self.api.get(data['trajectory_image']).content[:8],b'\x89PNG\r\n\x1a\n')
            self.assertIn('已知平台参数',send.call_args.args[1])
        response=self.api.post('/api/recognize',content=b'x'*(24*1024*1024+1))
        self.assertEqual(response.status_code,413)


if __name__=='__main__':unittest.main()
