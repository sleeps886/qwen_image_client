import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import load_environment,model_settings,model_timeout,model_request_options


class ConfigTests(unittest.TestCase):
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
            env.write_text('\ufeffMODEL_API_KEY="fake-${literal}-key"\nMODEL_NAME=file-model\nMODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n',encoding='utf-8')
            with patch.dict(os.environ,{'MODEL_NAME':'deployment-model'},clear=True):
                load_environment(env)
                settings=model_settings()
                self.assertEqual(settings.api_key,'fake-${literal}-key')
                self.assertEqual(settings.model,'deployment-model')
                self.assertNotIn(settings.api_key,repr(settings))


if __name__=='__main__':unittest.main()
