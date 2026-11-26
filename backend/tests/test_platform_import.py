import copy
import json
import unittest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.tests.test_prediction_platform import navigation


class PlatformImportTests(unittest.TestCase):
    def upload(self,data):
        with TestClient(app,base_url='http://127.0.0.1') as client:
            return client.post('/api/incremental/import-platform',files={
                'file':('platform.json',json.dumps(data).encode(),'application/json')})

    def test_platform_object_array_and_embedded_config(self):
        for data in (navigation(),navigation()['platforms'],{'platform_config':navigation()}):
            response=self.upload(data)
            self.assertEqual(response.status_code,200,response.text)
            platform=response.json()['platform_config']['platforms'][0]
            self.assertEqual(platform['start_ll'],[30.0623,122.0415])
            self.assertEqual(platform['tow_m'],[-150,0])

    def test_color_preserved_and_bad_color_ignored(self):
        for color,expected in (('#ffd900','#ffd900'),('url(bad)',None)):
            data=navigation();data['platforms'][0]['color']=color
            response=self.upload(data)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['platform_config']['platforms'][0].get('color'),expected)

    def test_invalid_platforms_rejected(self):
        duplicate=navigation();duplicate['platforms'].append(copy.deepcopy(duplicate['platforms'][0]))
        missing=navigation();del missing['platforms'][0]['speed_kn']
        for data in ({'evaluation_only':True,'points':[]},duplicate,missing,{'platforms':[]}, {'targets':[]}):
            self.assertEqual(self.upload(data).status_code,400)

    def test_invalid_json_and_size_rejected(self):
        with TestClient(app,base_url='http://127.0.0.1') as client:
            for raw in (b'not json',b' ' * 100001):
                response=client.post('/api/incremental/import-platform',files={'file':('platform.json',raw,'application/json')})
                self.assertEqual(response.status_code,400,response.text)
