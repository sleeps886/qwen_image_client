"""Guard the new boundaries so future graph integration stays replaceable."""
import ast
import importlib.util
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.api.dependencies import get_sonar_agent
from backend.app.api.routers import recognition
from backend.app.core.paths import PROJECT_ROOT,STATIC_DIR,OUTPUTS_DIR,UPLOADS_DIR
from backend.app.core.exceptions import ResultNotFound
from backend.app.models.model_settings import ModelSettings
from backend.app.repositories.result_repository import ResultRepository
from backend.app.services.inputs.input_service import prepare_input
from backend.app.agents.sonar_agent import SonarAgent
from backend.tests.test_meta_tracks import meta_fixture


class ArchitectureTests(unittest.TestCase):
    def test_factory_routes_assets_and_openapi_contract(self):
        application=create_app()
        with TestClient(application,base_url='http://127.0.0.1') as api:
            self.assertEqual(api.get('/').status_code,200)
            self.assertIn('输入来源',api.get('/').text)
            for name in ('app.js','style.css'):self.assertEqual(api.get('/assets/'+name).status_code,200)
            paths=api.get('/openapi.json').json()['paths']
            self.assertEqual(api.get('/api/health').json()['workflow_engine'],'langgraph-evidence-v1')
            self.assertTrue(api.get('/api/health').json()['langgraph_enabled'])
            self.assertEqual(set(paths),{'/api/data-example','/api/health','/api/logs/{request_id}',
                '/api/logs/{request_id}/download','/api/recognize','/api/recognize/stream',
                '/api/results/{result_id}/{filename}','/api/history','/api/history/{result_id}',
            '/api/comparisons/{result_id}/{comparison_id}/{filename}','/api/compare','/api/evaluate',
            '/api/invert','/api/invert/stream','/api/incremental/sessions',
            '/api/incremental/sessions/{session_id}','/api/incremental/sessions/{session_id}/step'})
            self.assertEqual(api.get('/assets/config.py').status_code,404)

    def test_paths_remain_compatible_with_existing_runtime_data(self):
        self.assertEqual(STATIC_DIR,PROJECT_ROOT/'frontend')
        self.assertEqual(OUTPUTS_DIR,PROJECT_ROOT/'outputs')
        self.assertEqual(UPLOADS_DIR,PROJECT_ROOT/'work')
        self.assertTrue((STATIC_DIR/'index.html').is_file())
        self.assertTrue((PROJECT_ROOT/'启动工具.cmd').is_file())
        self.assertTrue((PROJECT_ROOT/'backend/app/agents/sonar_agent.py').is_file())
        self.assertFalse((PROJECT_ROOT/'backend/app/workflows').exists())
        self.assertFalse((PROJECT_ROOT/'app').is_dir())
        self.assertFalse((PROJECT_ROOT/'backend/app/static').exists())
        self.assertEqual((PROJECT_ROOT/'requirements.txt').read_text(encoding='utf-8').splitlines()[-1],'-r backend/requirements.txt')
        self.assertTrue((PROJECT_ROOT/'frontend/tests/test_frontend_stream.cjs').is_file())
        spec=importlib.util.spec_from_file_location('compatibility_launcher_test',PROJECT_ROOT/'app.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        self.assertTrue(callable(launcher.main))

    def test_services_and_nodes_do_not_depend_on_fastapi(self):
        for folder in ('services','agents','repositories','models'):
            for path in (PROJECT_ROOT/'backend/app'/folder).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    if isinstance(node,ast.ImportFrom):
                        self.assertFalse((node.module or '').startswith(('fastapi','starlette')),str(path))

    def test_services_are_grouped_by_responsibility(self):
        root=PROJECT_ROOT/'backend/app/services'
        expected={'inputs','jobs','measurements','association','localization',
                  'quality','rendering','evaluation','knowledge'}
        packages={path.name for path in root.iterdir() if path.is_dir() and (path/'__init__.py').is_file()}
        self.assertEqual(packages,expected)
        self.assertEqual({path.name for path in root.glob('*.py')},{'__init__.py'})

    def test_input_validation_does_not_depend_on_rendering_or_localization(self):
        root=PROJECT_ROOT/'backend/app/services/inputs'
        for path in root.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith((
                        'backend.app.services.rendering','backend.app.services.localization',
                        'matplotlib','backend.app.api')),str(path))

    def test_platform_and_track_validation_are_not_defined_in_renderer(self):
        root=PROJECT_ROOT/'backend/app/services'
        def functions(path):
            return {node.name for node in ast.parse(path.read_text(encoding='utf-8')).body
                    if isinstance(node,ast.FunctionDef)}
        self.assertEqual(functions(root/'inputs/platform_input.py'),{'platform_context'})
        self.assertEqual(functions(root/'inputs/track_validation.py'),{'parse_tracks'})
        self.assertEqual(functions(root/'rendering/trajectory.py'),{'render_tracks','render_document'})

    def test_workflow_nodes_can_run_without_http_or_credentials_in_state(self):
        calls=[]
        class FakeGateway:
            def invoke(self,images,prompt,settings,output_root,mode):
                calls.append((images,prompt,mode))
                return {'answer':'{}','output_dir':str(output_root),'usage':None}
        workflow=SonarAgent(gateway=FakeGateway(),renderer=lambda answer,directory,context:{'tracks':[]})
        settings=ModelSettings('mock-model','https://invalid.test','fake-private-key')
        state=workflow.input_state([Path('example.png')],'test','images')
        self.assertNotIn('fake-private-key',json.dumps(state))
        result=workflow.invoke([Path('example.png')],'test',settings,PROJECT_ROOT/'work','images')
        self.assertEqual(calls,[(['example.png'],'test','images')])
        self.assertEqual(workflow.render(result['answer'],result['output_dir']),{'tracks':[]})

    def test_input_service_accepts_plain_bytes_and_meta_without_upload_objects(self):
        prepared=prepare_input(0,json.dumps(meta_fixture()).encode(),'test','data','meta.json')
        self.assertEqual(prepared.mode,'data')
        self.assertEqual(prepared.input_data['points'],42)

    def test_repository_create_save_read_and_invalid_paths(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT/'backend/tests') as folder:
            repo=ResultRepository(folder);run=repo.create_run()
            repo.save(run,'answer.txt','test')
            self.assertEqual(repo.file(run.name,'answer.txt').read_text(encoding='utf-8'),'test')
            for name in ('../config.py','.env','unknown.json'):
                with self.assertRaises(ResultNotFound):repo.file(run.name,name)
            with self.assertRaises(ResultNotFound):repo.file('../outputs','answer.txt')

    def test_history_keeps_usage_and_optional_debug_downloads(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT/'backend/tests') as folder:
            repo=ResultRepository(folder);run=repo.create_run()
            repo.save(run,'answer.txt','test');repo.save(run,'tracks.json',{'coordinate_system':'lon_lat'})
            (run/'estimated_track.png').write_bytes(b'placeholder-for-repository-test')
            usage={'input_tokens':2080,'output_tokens':2962,'cost':'¥0.009662'}
            repo.save(run,'response.json',{'usage':usage})
            repo.save(run,'provider_reasoning.txt','existing provider text')
            saved=repo.historical_result(run.name)
            self.assertEqual(saved['usage'],usage)
            self.assertIn('response.json',saved['files']);self.assertIn('provider_reasoning.txt',saved['files'])
            repo.save(run,'response.json','malformed')
            self.assertIsNone(repo.historical_result(run.name)['usage'])

    def test_fastapi_workflow_dependency_is_replaceable_and_preflight_never_invokes(self):
        class NoModelWorkflow:
            def invoke(self,*args,**kwargs):raise AssertionError('dry-run called a model')
        application=create_app();application.dependency_overrides[get_sonar_agent]=NoModelWorkflow
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT/'backend/tests') as folder,patch.object(recognition,'UPLOADS',Path(folder)),TestClient(application,base_url='http://127.0.0.1') as api:
            response=api.post('/api/recognize',data={'dry_run':'true','input_mode':'data'},
                files={'platform':('meta.json',json.dumps(meta_fixture()).encode(),'application/json')})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['input_data']['points'],42)


class WorkflowStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_gateway_streams_without_fastapi(self):
        class FakeGateway:
            async def stream(self,*args):
                yield {'event':'delta','text':'test'}
                await asyncio.sleep(0)
                yield {'event':'model_complete','result':{'answer':'test'}}
        workflow=SonarAgent(gateway=FakeGateway())
        events=[event async for event in workflow.stream([],'test',ModelSettings('mock','unused','fake'),PROJECT_ROOT/'work','data')]
        self.assertEqual([e['event'] for e in events],['delta','model_complete'])
