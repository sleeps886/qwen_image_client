import json
import os
from pathlib import Path
import tempfile
import unittest
import threading
import uuid
import socket
import time
import asyncio
import httpx
import uvicorn
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.paths import PROJECT_ROOT
from backend.app.api.routers import recognition as web,results as results_api,evaluation as evaluation_api
from backend.app.infrastructure.llm import client as llm_client,stream_client as llm_stream
from backend.tests.test_client import PNG


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root.joinpath('uploads').mkdir()
        self.patches=[patch.object(results_api,'OUTPUTS',self.root/'outputs'),patch.object(evaluation_api,'OUTPUTS',self.root/'outputs'),patch.object(web,'UPLOADS',self.root/'uploads'),patch.object(web,'OUTPUTS',self.root/'outputs'),
                      patch.dict(os.environ,{'MODEL_API_KEY':'fake-server-key','MODEL_NAME':'server-model',
                                            'MODEL_API_FORMAT':'openai','MODEL_AUTH_MODE':'bearer',
                                            'MODEL_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'})]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        self.api=TestClient(app,base_url='http://127.0.0.1')
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
        with patch.object(llm_client,'send_images') as send:
            response=self.post({'dry_run':'true'},name='../../hull.png')
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['dry_run'])
            self.assertNotIn('..',response.json()['images'][0]['name'])
            send.assert_not_called()
        self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_image_mode_with_meta_has_actionable_mode_error(self):
        from backend.tests.test_meta_tracks import meta_fixture
        with patch.object(llm_client,'send_images') as send,patch.object(llm_stream,'stream_images') as stream:
            for route,params in (('/api/recognize',{'dry_run':'true'}),('/api/recognize/stream',{'consent':'true'})):
                response=self.api.post(route,data={**params,'input_mode':'images'},files=[
                    ('files',('hull.png',PNG,'image/png')),
                    ('platform',('meta.json',json.dumps(meta_fixture()).encode(),'application/json'))])
                self.assertEqual(response.status_code,400,response.text)
                self.assertIn('单文件绘图',response.json()['detail'])
            send.assert_not_called();stream.assert_not_called()

    def test_single_file_preflight_auto_mode_without_images(self):
        from backend.tests.test_trajectory import CONTEXT
        with patch.object(llm_client,'send_images') as send:
            response=self.api.post('/api/recognize',data={'dry_run':'true'},
                files={'platform':('platform.json',json.dumps(CONTEXT).encode(),'application/json')})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['input_mode'],'data')
            self.assertEqual(response.json()['images'],[])
            self.assertEqual(response.json()['input_data']['objects'],1)
            send.assert_not_called()
        self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_semantic_single_file_preflight_and_model_render(self):
        from backend.tests.test_data_input import semantic_fixture
        from backend.app.services.data_input import prepare_data
        raw=json.dumps(semantic_fixture()).encode();_,answer,_=prepare_data(raw)
        file={'platform':('semantic.json',raw,'application/json')}
        with patch.object(llm_client,'send_images') as send:
            check=self.api.post('/api/recognize',data={'dry_run':'true'},files=file)
            self.assertEqual(check.status_code,200,check.text)
            self.assertEqual(check.json()['input_data']['source_type'],'semantic_summary')
            send.assert_not_called()
        rid='20261116_120000_1234eeee';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'answer.txt').write_text(answer,encoding='utf-8')
        with patch.object(llm_client,'send_images',return_value={'answer':answer,'output_dir':str(root),'usage':{}}):
            response=self.api.post('/api/recognize',data={'consent':'true','input_mode':'data'},files=file)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['input_data']['plot_kind'],'endpoints')
        self.assertEqual(self.api.get(data['trajectory_image']).content[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(self.api.get('/api/history/'+rid).json()['input_data']['plot_kind'],'endpoints')

    def test_anthropic_stream_end_to_end_with_mock_transport(self):
        from backend.app.services.data_input import prepare_data
        raw=(PROJECT_ROOT/'examples/target.json').read_bytes();_,answer,_=prepare_data(raw)
        real_client=httpx.AsyncClient
        calls=[]
        async def handle(request):
            calls.append(request)
            self.assertEqual(request.url.path,'/v1/messages')
            self.assertEqual(request.headers['anthropic-version'],'2023-06-01')
            self.assertEqual(request.headers['authorization'],'Bearer fake-server-key')
            body=json.loads(await request.aread())
            self.assertEqual(body['messages'][0]['role'],'user')
            self.assertNotIn('reasoning_effort',body)
            chunks=[{'type':'message_start','message':{'id':'msg_e2e','usage':{'input_tokens':1},'content':[]}},
                {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':answer}},
                {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':1}},
                {'type':'message_stop'}]
            content=''.join('data: '+json.dumps(x,ensure_ascii=False)+'\n\n' for x in chunks)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content.encode())
        with patch.dict(os.environ,{'MODEL_API_FORMAT':'anthropic','MODEL_AUTH_MODE':'bearer',
                'MODEL_BASE_URL':'https://www.dmxapi.cn/v1'}),patch('backend.app.infrastructure.llm.stream_client.httpx.AsyncClient',
                side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            response=self.api.post('/api/recognize/stream',data={'consent':'true','input_mode':'data'},
                files={'platform':('target.json',raw,'application/json')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(calls),1)
        self.assertIn('event: result',response.text)
        self.assertNotIn('event: error',response.text)
        self.assertTrue(list((self.root/'outputs').glob('*/estimated_track.png')))

    def test_meta_preflight_and_both_render_routes_use_full_samples(self):
        from backend.app.services.data_input import prepare_data
        from backend.tests.test_meta_tracks import meta_fixture
        raw=json.dumps(meta_fixture()).encode();_,answer,_=prepare_data(raw,'meta.json')
        file={'platform':('meta.json',raw,'application/json')}
        with patch.object(llm_client,'send_images') as send,patch.object(llm_stream,'stream_images') as stream:
            response=self.api.post('/api/recognize',data={'dry_run':'true'},files=file)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['input_data']['points'],42)
            self.assertEqual(response.json()['input_data']['model_checkpoint_points'],18)
            send.assert_not_called();stream.assert_not_called()
        for streaming in (False,True):
            rid='20261116_120000_'+uuid.uuid4().hex[:8];root=self.root/'outputs'/rid;root.mkdir(parents=True)
            result={'answer':answer,'output_dir':str(root),'usage':{},'finish_reason':'stop','has_reasoning':False}
            async def fake_stream(*args,**kwargs):
                yield {'event':'delta','text':answer}
                yield {'event':'model_complete','result':result}
            (root/'answer.txt').write_text(answer,encoding='utf-8')
            with patch.object(llm_client,'send_images',return_value=result),patch.object(llm_stream,'stream_images',side_effect=fake_stream):
                response=self.api.post('/api/recognize/stream' if streaming else '/api/recognize',
                    data={'consent':'true','input_mode':'data'},files=file)
            self.assertEqual(response.status_code,200,response.text)
            if streaming:self.assertIn('event: result',response.text)
            else:self.assertTrue(response.json()['trajectory_image'])
            saved=json.loads((root/'tracks.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['plot_kind'],'meta_reconstruction')
            self.assertEqual(sum(len(t['points']) for t in saved['tracks']),42)
            self.assertEqual(self.api.get('/api/history/'+rid).json()['input_data']['plot_kind'],'meta_reconstruction')

    def test_single_file_sync_calls_text_model_and_renders(self):
        from backend.tests.test_trajectory import CONTEXT
        from backend.app.services.data_input import prepare_data
        raw=json.dumps(CONTEXT).encode();_,answer,_=prepare_data(raw)
        rid='20261116_120000_1234dddd';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'answer.txt').write_text(answer,encoding='utf-8')
        with patch.object(llm_client,'send_images',return_value={'answer':answer,'output_dir':str(root),'usage':{}}) as send:
            response=self.api.post('/api/recognize',data={'consent':'true','input_mode':'data'},
                files={'platform':('platform.json',raw,'application/json')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(send.call_args.args[0],[])
        self.assertTrue(send.call_args.kwargs['allow_text_only'])
        self.assertIn('没有上传图片',send.call_args.args[1])
        data=response.json();self.assertEqual(data['input_mode'],'data')
        self.assertEqual(self.api.get(data['trajectory_image']).content[:8],b'\x89PNG\r\n\x1a\n')
        saved=self.api.get(data['downloads']['tracks.json']).json()
        self.assertEqual(saved['input_mode'],'data')
        self.assertEqual(self.api.get('/api/history/'+rid).json()['input_mode'],'data')

    def test_single_file_stream_js_renders_without_images(self):
        from backend.app.services.data_input import prepare_data
        raw=(PROJECT_ROOT/'examples/target.json').read_bytes();_,answer,_=prepare_data(raw)
        rid='20261116_120000_1111dddd';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        async def fake_stream(paths,prompt,*args,**kwargs):
            self.assertEqual(paths,[]);self.assertTrue(kwargs['allow_text_only'])
            yield {'event':'delta','text':answer}
            yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(root),'usage':{},'finish_reason':'stop','has_reasoning':False}}
        with patch.object(llm_stream,'stream_images',side_effect=fake_stream):
            response=self.api.post('/api/recognize/stream',data={'consent':'true','input_mode':'data'},
                files={'platform':('points.js',b'export default '+raw+b';','application/javascript')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('event: result',response.text)
        self.assertTrue((root/'estimated_track.png').is_file())
        self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_data_invalid_and_consent_never_calls_cloud(self):
        with patch.object(llm_client,'send_images') as send,patch.object(llm_stream,'stream_images') as stream:
            for endpoint in ('/api/recognize','/api/recognize/stream'):
                for params in ({'input_mode':'data'},{'input_mode':'data','consent':'true'}):
                    response=self.api.post(endpoint,data=params)
                    self.assertEqual(response.status_code,400,response.text)
                response=self.api.post(endpoint,data={'consent':'true','input_mode':'data'},
                    files={'platform':('bad.js',b'const x = alert(1);','application/javascript')})
                self.assertEqual(response.status_code,400,response.text)
                response=self.api.post(endpoint,data={'consent':'true','input_mode':'data'},
                    files={'files':('hull.png',PNG,'image/png')})
                self.assertEqual(response.status_code,400,response.text)
            send.assert_not_called();stream.assert_not_called()
        self.assertEqual(self.api.get('/api/data-example').status_code,200)

    def test_send_and_download(self):
        rid='20261116_120000_abcdef12'
        output=self.root/'outputs'/rid;output.mkdir(parents=True)
        (output/'answer.txt').write_text('两条亮带',encoding='utf-8')
        with patch.object(llm_client,'send_images',return_value={'answer':'两条亮带','output_dir':str(output),'usage':{'total_tokens':7}}) as send:
            response=self.post({'consent':'true'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(send.call_args.args[4],'fake-server-key')
            self.assertEqual(send.call_args.args[2],'server-model')
            data=response.json();self.assertEqual(data['result_id'],rid)
            self.assertEqual(self.api.get(data['downloads']['answer.txt']).text,'两条亮带')
        self.assertEqual(self.api.get('/api/results/bad/answer.txt').status_code,404)

    def test_reject_invalid_and_secret_validation(self):
        with patch.object(llm_client,'send_images') as send:
            self.assertEqual(self.post().status_code,400)
            self.assertEqual(self.post({'dry_run':'true'},raw=b'not an image').status_code,400)
            response=self.post({'dry_run':'fake-secret-key'})
            self.assertEqual(response.status_code,422)
            self.assertNotIn('fake-secret-key',response.text)
            self.assertEqual(self.post({'dry_run':'true'},headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(self.post({'dry_run':'true','base_url':'https://evil.example'}).status_code,400)
            send.assert_not_called()

    def test_server_config_override_and_missing_key(self):
        with patch.object(llm_client,'send_images') as send:
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

    def test_live_http_first_delta_before_completion(self):
        from backend.tests.test_trajectory import result
        release=threading.Event()
        rid='20261116_120000_7777aabb';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        answer=json.dumps(result('bearing_time'))
        async def upstream(*args,**kwargs):
            yield {'event':'delta','text':answer[:20]}
            while not release.is_set():await asyncio.sleep(.01)
            yield {'event':'delta','text':answer[20:]}
            yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(root),'usage':None,'finish_reason':'stop','has_reasoning':False}}
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False,lifespan='off'))
        with patch.object(llm_stream,'stream_images',side_effect=upstream):
            thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
            try:
                limit=time.monotonic()+5
                while not server.started and time.monotonic()<limit:time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(timeout=10,trust_env=False) as client:
                    with client.stream('POST',f'http://127.0.0.1:{port}/api/recognize/stream',data={'consent':'true'},files={'files':('hull.png',PNG,'image/png')}) as response:
                        lines=response.iter_lines();first=False;remaining=[]
                        for line in lines:
                            if line=='event: delta':
                                self.assertFalse(release.is_set())
                                first=True;release.set();break
                        self.assertTrue(first)
                        remaining=list(lines)
                        self.assertIn('event: result',remaining)
            finally:
                release.set();server.should_exit=True;thread.join(10);sock.close()

    def test_streaming_route_final_result_and_logs(self):
        from backend.tests.test_trajectory import result
        rid='20261116_120000_1111eeee';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        answer=json.dumps(result('bearing_time'))
        async def fake_stream(*args,**kwargs):
            yield {'event':'phase','phase':'connected','message':'测试SSE已连接'}
            yield {'event':'delta','text':answer[:20]}
            yield {'event':'delta','text':answer[20:]}
            yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(root),'usage':{},'finish_reason':'stop','has_reasoning':False}}
        with patch.object(llm_stream,'stream_images',side_effect=fake_stream):
            response=self.api.post('/api/recognize/stream',data={'consent':'true'},files={'files':('hull.png',PNG,'image/png')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('text/event-stream',response.headers['content-type'])
        self.assertIn('event: delta',response.text)
        self.assertIn('event: result',response.text)
        self.assertTrue(self.api.get('/api/logs/'+response.headers['X-Request-ID']).json()['done'])
        self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_stream_error_is_explicit_event(self):
        async def failed(*args,**kwargs):
            yield {'event':'phase','phase':'connecting','message':'测试连接'}
            raise web.ClientError('模拟中转站拒绝')
        with patch.object(llm_stream,'stream_images',side_effect=failed):
            response=self.api.post('/api/recognize/stream',data={'consent':'true'},files={'files':('hull.png',PNG,'image/png')})
        self.assertIn('event: error',response.text)
        self.assertNotIn('event: result',response.text)

    def test_upstream_timeout_is_504(self):
        with patch.object(llm_client,'send_images',side_effect=web.UpstreamTimeout('模拟上游超时')) as send:
            response=self.post({'consent':'true'})
            self.assertEqual(response.status_code,504)
            self.assertIn('timeout',send.call_args.kwargs)
            self.assertIsNone(send.call_args.kwargs['timeout'])

    def test_structured_evaluation_is_local(self):
        from backend.tests.test_evaluation import prediction,truth,semantic
        rid='20261116_120000_4433ccdd';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'tracks.json').write_text(json.dumps(prediction()))
        with patch.object(llm_client,'send_images') as send:
            response=self.api.post('/api/evaluate',data={'result_id':rid},files=[
                ('semantic',('semantic.json',semantic(),'application/json')),
                ('truth',('truth.jsonl',truth(),'text/plain'))])
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['position_metrics']['rmse_m'],0)
            self.assertEqual(self.api.get(data['report_url']).status_code,200)
            send.assert_not_called()

    def test_local_comparison_and_reject_bearing(self):
        from backend.tests.test_comparison import drawing
        rid='20261116_120000_9988aabb'
        root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'estimated_track.png').write_bytes(drawing('line'))
        (root/'tracks.json').write_text(json.dumps({'coordinate_system':'local_xy_m'}))
        with patch.object(llm_client,'send_images') as send:
            response=self.api.post('/api/compare',data={'result_id':rid},files={'reference':('track.png',drawing('line'),'image/png')})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['similarity_percent'],100)
            self.assertEqual(self.api.get(data['overlay_url']).content[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(self.api.get(data['report_url']).json()['result_id'],rid)
            (root/'tracks.json').write_text(json.dumps({'coordinate_system':'bearing_time'}))
            response=self.api.post('/api/compare',data={'result_id':rid},files={'reference':('track.png',drawing('line'),'image/png')})
            self.assertEqual(response.status_code,400)
            response=self.api.post('/api/compare',data={'result_id':rid,'reference_kind':'bearing_time'},files={'reference':('bearing.png',drawing('line'),'image/png')})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['predicted_kind'],'bearing_time')
            send.assert_not_called()

    def test_import_generated_history(self):
        from backend.tests.test_comparison import drawing
        rid='20261116_120000_aaaabbbb';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        (root/'estimated_track.png').write_bytes(drawing('line'))
        (root/'tracks.json').write_text(json.dumps({'coordinate_system':'bearing_time','tracks':[],'warnings':['非地理轨迹']}))
        (root/'answer.txt').write_text('已生成模型正文',encoding='utf-8')
        with patch.object(llm_client,'send_images') as send:
            self.assertEqual(self.api.get('/api/history').json()['items'][0]['result_id'],rid)
            data=self.api.get('/api/history/'+rid).json()
            self.assertEqual(data['trajectory_status'],'bearing_time')
            self.assertEqual(self.api.get(data['trajectory_image']).status_code,200)
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
        with patch.object(llm_client,'send_images',side_effect=fake):
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
        from backend.tests.test_evidence_agent import sonar_fixture,candidate_answer
        CONTEXT,images=sonar_fixture()
        rid='20261116_120000_1234abcd'
        directory=self.root/'outputs'/rid;directory.mkdir(parents=True)
        def reply(paths,prompt,*args,**kwargs):
            return {'answer':candidate_answer(prompt),'output_dir':str(directory),'usage':{}}
        with patch.object(llm_client,'send_images',side_effect=reply) as send:
            response=self.api.post('/api/recognize',data={'consent':'true'},files=[
                ('files',('btr_A_hull.png',images[0],'image/png')),
                ('files',('btr_A_tow.png',images[1],'image/png')),
                ('platform',('platform.json',json.dumps(CONTEXT).encode(),'application/json'))])
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()
            self.assertEqual(data['trajectory_status'],'position_estimate')
            self.assertEqual(self.api.get(data['trajectory_image']).content[:8],b'\x89PNG\r\n\x1a\n')
            self.assertIn('candidate_pairs',send.call_args.args[1])
            self.assertIn('quality_report.json',data['downloads'])
            self.assertIn('tracking_report.json',data['downloads'])
            self.assertIn('reprojection_report.json',data['downloads'])
            self.assertIn('target_count_report.json',data['downloads'])
            self.assertEqual(data['target_count']['associated_target_count'],1)
            self.assertEqual(data['target_count']['localized_target_count'],1)
            self.assertTrue(any(e['node']=='count_audit' and e['status']=='completed' for e in data['node_events']))
            self.assertIn('reprojection_0.png',data['downloads'])
            self.assertEqual(self.api.get(data['downloads']['reprojection_0.png']).status_code,200)
            self.assertEqual(self.api.get(data['downloads']['evidence_0.png']).status_code,200)
            send.reset_mock()
            preview=self.api.post('/api/recognize',data={'dry_run':'true'},files=[
                ('files',('btr_A_hull.png',images[0],'image/png')),
                ('files',('btr_A_tow.png',images[1],'image/png')),
                ('platform',('platform.json',json.dumps(CONTEXT).encode(),'application/json'))])
            self.assertEqual(preview.status_code,200,preview.text)
            self.assertIn('evidence_1.png',preview.json()['downloads'])
            send.assert_not_called()
        response=self.api.post('/api/recognize',content=b'x'*(24*1024*1024+1))
        self.assertEqual(response.status_code,413)

    def test_evidence_node_events_stream_before_final_result(self):
        from backend.tests.test_evidence_agent import sonar_fixture,candidate_answer
        context,images=sonar_fixture()
        rid='20261116_120000_3333eeee';root=self.root/'outputs'/rid;root.mkdir(parents=True)
        async def upstream(paths,prompt,*args,**kwargs):
            answer=candidate_answer(prompt)
            yield {'event':'delta','text':answer}
            yield {'event':'model_complete','result':{'answer':answer,'output_dir':str(root),'usage':{},'has_reasoning':False,'finish_reason':'stop'}}
        with patch.object(llm_stream,'stream_images',side_effect=upstream):
            response=self.api.post('/api/recognize/stream',data={'consent':'true'},files=[
                ('files',('btr_A_hull.png',images[0],'image/png')),
                ('files',('btr_A_tow.png',images[1],'image/png')),
                ('platform',('platform.json',json.dumps(context).encode(),'application/json'))])
        self.assertEqual(response.status_code,200,response.text)
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        nodes=[e for e in events if e.get('event')=='node']
        self.assertTrue(any(e['node']=='count_hypothesis' and e['status']=='running' for e in nodes))
        self.assertTrue(any(e['node']=='reprojection_quality_check' and e['status']=='completed' for e in nodes))
        result=next(e['data'] for e in events if e.get('event')=='result')
        self.assertEqual(result['target_count']['localized_target_count'],1)
        self.assertEqual(len(result['node_events']),28)
        self.assertLess(next(i for i,e in enumerate(events) if e.get('event')=='node'),next(i for i,e in enumerate(events) if e.get('event')=='result'))


if __name__=='__main__':unittest.main()
