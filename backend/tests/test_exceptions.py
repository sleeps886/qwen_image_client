"""Business errors preserve HTTP contracts and release inverse job resources."""
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api.exception_handlers import application_error_handler
from backend.app.core.exceptions import (
    ApplicationError,InputError,NotFoundError,PayloadTooLargeError,ProcessingError,
    BusyError,LocalStorageError,ModelServiceError,ModelTimeoutError,ClientError,
    UpstreamTimeout,UpstreamConnectionError,ResultNotFound,InvalidStoredResult,
    ReviewConsentRequired,InvalidImagePair,UnsupportedRendererProfile,
    UnsupportedPlatformCount,InvalidImageFormat,ReviewCredentialsRequired,InversePreparationError,
)
from backend.app.services.jobs import inverse_jobs


class ErrorHandlerTests(unittest.TestCase):
    def test_status_categories_and_legacy_detail_contract(self):
        cases=[(InputError,400),(NotFoundError,404),(PayloadTooLargeError,413),
               (ProcessingError,422),(BusyError,429),(LocalStorageError,500),
               (ModelServiceError,502),(ModelTimeoutError,504),(ClientError,400),
               (UpstreamTimeout,504),(UpstreamConnectionError,502),(ResultNotFound,404),
               (InvalidStoredResult,400)]
        application=FastAPI()
        application.add_exception_handler(ApplicationError,application_error_handler)
        for index,(kind,status) in enumerate(cases):
            def fail(error_type=kind):raise error_type('safe message')
            application.add_api_route('/error/'+str(index),fail,methods=['GET'])
        with TestClient(application) as client:
            for index,(kind,status) in enumerate(cases):
                with self.subTest(kind=kind):
                    response=client.get('/error/'+str(index))
                    self.assertEqual(response.status_code,status)
                    self.assertEqual(response.json(),{'detail':'safe message'})

    def test_structured_processing_detail_remains_unchanged(self):
        application=FastAPI()
        application.add_exception_handler(ApplicationError,application_error_handler)
        detail={'message':'partial observations','result_id':'example','automatic_retry':False}
        @application.get('/error')
        def fail():raise ProcessingError(detail)
        with TestClient(application) as client:
            response=client.get('/error')
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json(),{'detail':detail})


class InverseJobErrorTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.slots=threading.BoundedSemaphore(1)
        self.files=[self.upload('hull.png'),self.upload('tow.png')]
        self.platform=SimpleNamespace(file=io.BytesIO(b'{}'))
        self.prepared=SimpleNamespace(context={'platforms':[{}]})
        mock=patch.object(inverse_jobs,'prepare_input',return_value=self.prepared)
        mock.start();self.addCleanup(mock.stop)

    @staticmethod
    def upload(name,format='PNG'):
        data=io.BytesIO();Image.new('RGB',(2,2)).save(data,format=format);data.seek(0)
        return SimpleNamespace(filename=name,file=data)

    def prepare(self,**kwargs):
        return inverse_jobs.prepare_inverse_job(self.files,self.platform,
            kwargs.get('profile','btr_gui_v1'),kwargs.get('review',False),kwargs.get('consent',False),
            uploads_dir=self.root/'uploads',slots=self.slots)

    def assert_closed_and_released(self):
        self.assertTrue(all(file.file.closed for file in self.files))
        self.assertTrue(self.platform.file.closed)
        self.assertEqual(self.slots._value,1)
        if (self.root/'uploads').exists():self.assertEqual(list((self.root/'uploads').iterdir()),[])

    def test_consent_error_is_typed_and_closes_uploads(self):
        with self.assertRaises(ReviewConsentRequired):self.prepare(review=True)
        self.assert_closed_and_released()

    def test_image_pair_error_is_typed(self):
        self.files.pop().file.close()
        with self.assertRaises(InvalidImagePair):self.prepare()
        self.assert_closed_and_released()

    def test_busy_does_not_release_another_jobs_slot(self):
        self.slots.acquire()
        try:
            with self.assertRaises(BusyError):self.prepare()
            self.assertEqual(self.slots._value,0)
        finally:self.slots.release()
        self.assert_closed_and_released()

    def test_unsupported_profile(self):
        with self.assertRaises(UnsupportedRendererProfile):self.prepare(profile='unknown')
        self.assert_closed_and_released()

    def test_unsupported_platform_count(self):
        self.prepared.context['platforms']=[{},{}]
        with self.assertRaises(UnsupportedPlatformCount):self.prepare()
        self.assert_closed_and_released()

    def test_image_size_error_preserves_413(self):
        with patch.object(inverse_jobs,'MAX_FILE_BYTES',1):
            with self.assertRaises(PayloadTooLargeError):self.prepare()
        self.assert_closed_and_released()

    def test_image_format_error(self):
        self.files[0].file.close();self.files[0]=self.upload('hull.bmp','BMP')
        with self.assertRaises(InvalidImageFormat):self.prepare()
        self.assert_closed_and_released()

    def test_review_missing_credentials(self):
        with patch.object(inverse_jobs,'model_settings',return_value=SimpleNamespace(
                api_key='',base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',api_format='openai')):
            with self.assertRaises(ReviewCredentialsRequired):self.prepare(review=True,consent=True)
        self.assert_closed_and_released()

    def test_os_error_does_not_expose_internal_paths(self):
        with patch.object(inverse_jobs,'prepare_input',side_effect=OSError('private path')):
            with self.assertRaises(InversePreparationError) as caught:self.prepare()
        self.assertNotIn('private path',str(caught.exception))
        self.assert_closed_and_released()

    def test_success_keeps_slot_until_idempotent_close(self):
        job=self.prepare()
        self.assertEqual(self.slots._value,0)
        self.assertTrue(all(path.is_file() for path in job.paths))
        job.close();job.close()
        self.assert_closed_and_released()
