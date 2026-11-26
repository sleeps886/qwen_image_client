import json
import urllib.error
from unittest.mock import patch
import pytest
from backend.app.infrastructure.llm.client import send_images
from backend.app.core.exceptions import UpstreamConnectionError


def test_transport_failure_records_only_type_and_numeric_code(tmp_path):
    private='https://secret-token.example.invalid/path?key=private'
    with patch('backend.app.infrastructure.llm.client.urllib.request.build_opener') as opener:
        opener.return_value.open.side_effect=urllib.error.URLError(OSError(10055,private))
        with pytest.raises(UpstreamConnectionError) as caught:
            send_images([], 'test', 'offline', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'private', tmp_path,
                allow_text_only=True,api_format='openai')
    error=caught.value
    assert error.transport_diagnostic=={'cause_type':'OSError','errno':10055,'timed_out':False}
    assert 'secret' not in str(error) and 'private' not in json.dumps(error.transport_diagnostic)
