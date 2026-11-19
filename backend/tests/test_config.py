import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.core.config import Settings,load_environment,model_settings,model_timeout,model_request_options


class ConfigTests(unittest.TestCase):
    def test_server_defaults(self):
        with patch.dict(os.environ,{},clear=True):
            settings=Settings.from_environment()
        self.assertEqual(settings.host,'127.0.0.1')
        self.assertEqual(settings.port,8870)
        self.assertEqual(settings.allowed_hosts,('127.0.0.1','localhost','[::1]'))

    def test_server_environment(self):
        with patch.dict(os.environ,{'APP_HOST':'0.0.0.0','APP_PORT':'9001',
                'APP_ALLOWED_HOSTS':' localhost,192.168.1.20,localhost '},clear=True):
            settings=Settings.from_environment()
        self.assertEqual(settings.host,'0.0.0.0')
        self.assertEqual(settings.port,9001)
        self.assertEqual(settings.allowed_hosts,('localhost','192.168.1.20'))

    def test_invalid_server_environment(self):
        for key,values in {
            'APP_HOST':('', 'http://localhost','localhost:8870','*','[::1','[127.0.0.1]'),
            'APP_PORT':('', 'bad','0','65536','3.5'),
            'APP_ALLOWED_HOSTS':('', '*','localhost,','https://localhost','localhost:8870'),
        }.items():
            for value in values:
                with self.subTest(key=key,value=value),patch.dict(os.environ,{key:value},clear=True):
                    with self.assertRaises(ValueError):Settings.from_environment()

    def test_server_settings_used_by_app_and_launcher(self):
        import importlib
        from fastapi.testclient import TestClient
        module=importlib.import_module('backend.app.main')
        custom=Settings(host='0.0.0.0',port=9001,allowed_hosts=('sonar.local',))
        with patch.object(module,'settings',custom):
            application=module.create_app()
            with TestClient(application,base_url='http://sonar.local') as api:
                self.assertEqual(api.get('/openapi.json').status_code,200)
                self.assertEqual(api.get('/openapi.json',headers={'host':'untrusted.local'}).status_code,400)
                self.assertEqual(api.post('/api/recognize',headers={'origin':'http://other.local'}).status_code,403)
            with patch.object(module.uvicorn,'run') as run,patch.object(module,'configure_access_logging'):
                module.main()
                run.assert_called_once_with(module.app,host='0.0.0.0',port=9001)

    def test_reasoning_effort(self):
        with patch.dict(os.environ,{'MODEL_REASONING_EFFORT':'low'}):
            self.assertEqual(model_request_options(),{'reasoning_effort':'low'})
        with patch.dict(os.environ,{'MODEL_REASONING_EFFORT':''}):
            self.assertEqual(model_request_options(),{})
        with patch.dict(os.environ,{'MODEL_REASONING_EFFORT':'disabled'}):
            with self.assertRaises(ValueError):model_request_options()
    def test_timeout_config(self):
        for value in ('0','300','601','bad'):
            with patch.dict(os.environ,{'MODEL_TIMEOUT_SECONDS':value}):
                self.assertIsNone(model_timeout())
    def test_generic_names_override_legacy(self):
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'fake-legacy-key','QWEN_MODEL':'legacy-model',
                'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'},clear=True):
            self.assertEqual(model_settings().api_key,'fake-legacy-key')
            with patch.dict(os.environ,{'MODEL_API_KEY':'fake-relay-key','MODEL_NAME':'relay-model',
                    'MODEL_BASE_URL':'https://www.dmxapi.cn/v1'}):
                settings=model_settings()
                self.assertEqual(settings.api_key,'fake-relay-key')
                self.assertEqual(settings.model,'relay-model')
                self.assertEqual(settings.base_url,'https://www.dmxapi.cn/v1')
            with patch.dict(os.environ,{'MODEL_API_KEY':''}):
                self.assertEqual(model_settings().api_key,'')

    def test_dotenv_and_process_precedence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            env=Path(folder)/'.env'
            env.write_text('\ufeffMODEL_API_KEY="fake-${literal}-key"\nMODEL_NAME=file-model\nMODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\nAPP_HOST=localhost\nAPP_PORT=9000\nAPP_ALLOWED_HOSTS=localhost\n',encoding='utf-8')
            with patch.dict(os.environ,{'MODEL_NAME':'deployment-model','APP_PORT':'9001'},clear=True):
                load_environment(env)
                server=Settings.from_environment()
                self.assertEqual((server.host,server.port,server.allowed_hosts),('localhost',9001,('localhost',)))
                settings=model_settings()
                self.assertEqual(settings.api_key,'fake-${literal}-key')
                self.assertEqual(settings.model,'deployment-model')
                self.assertNotIn(settings.api_key,repr(settings))


if __name__=='__main__':unittest.main()
