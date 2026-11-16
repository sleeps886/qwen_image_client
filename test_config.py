import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import load_environment,model_settings


class ConfigTests(unittest.TestCase):
    def test_dotenv_and_process_precedence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            env=Path(folder)/'.env'
            env.write_text('\ufeffDASHSCOPE_API_KEY="fake-${literal}-key"\nQWEN_MODEL=file-model\nQWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n',encoding='utf-8')
            with patch.dict(os.environ,{'QWEN_MODEL':'deployment-model'},clear=True):
                load_environment(env)
                settings=model_settings()
                self.assertEqual(settings.api_key,'fake-${literal}-key')
                self.assertEqual(settings.model,'deployment-model')
                self.assertNotIn(settings.api_key,repr(settings))


if __name__=='__main__':unittest.main()
