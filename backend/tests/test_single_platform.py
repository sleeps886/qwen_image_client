import copy
import io
import json
from pathlib import Path
from threading import BoundedSemaphore
import tempfile
import unittest
from PIL import Image
from starlette.datastructures import UploadFile
from backend.app.services.inputs.input_service import prepare_input
from backend.app.core.exceptions import InputError
from backend.app.services.jobs.inverse_jobs import prepare_inverse_job
from backend.tests.test_full_inverse import inverse_fixture


class SinglePlatformTests(unittest.TestCase):
    def test_multiple_platforms_rejected_before_cloud(self):
        context=copy.deepcopy(inverse_fixture()[0]);context.pop('renderer_profile')
        other=copy.deepcopy(context['platforms'][0]);other['name']='B';context['platforms'].append(other)
        with self.assertRaisesRegex(ValueError,'仅分析单平台'):
            prepare_input(2,json.dumps(context).encode(),'','images')

    def test_legacy_display_field_still_supported_for_single_platform(self):
        context=copy.deepcopy(inverse_fixture()[0]);context.pop('renderer_profile');context['DB_STEP']=.25
        self.assertEqual(len(prepare_input(2,json.dumps(context).encode(),'','images').context['platforms']),1)

    def test_single_platform_mismatch_and_duplicate_arrays_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            buffer=io.BytesIO();Image.new('RGB',(100,100)).save(buffer,format='PNG')
            for names in [('btr_B_hull.png','btr_B_tow.png'),('btr_A_hull.png','btr_A_hull.png')]:
                uploads=[UploadFile(io.BytesIO(buffer.getvalue()),filename=n) for n in names]
                context=copy.deepcopy(inverse_fixture()[0]);context.pop('renderer_profile')
                metadata=UploadFile(io.BytesIO(json.dumps(context).encode()),filename='platform.json')
                slots=BoundedSemaphore(1)
                with self.subTest(names=names),self.assertRaises(InputError):
                    prepare_inverse_job(uploads,metadata,'btr_gui_v1',False,False,uploads_dir=Path(tmp),slots=slots)
                self.assertEqual(slots._value,1)


if __name__=='__main__':unittest.main()
