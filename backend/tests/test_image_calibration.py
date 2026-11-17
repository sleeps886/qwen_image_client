import json
import unittest

from backend.app.services.input_service import prepare_input
from backend.app.services.trajectory import parse_tracks, platform_context
from backend.tests.test_trajectory import CONTEXT, result


class ImageCalibrationTests(unittest.TestCase):
    def test_unlabelled_images_get_tool_calibration_even_with_old_prompt(self):
        prepared=prepare_input(2,None,'旧提示词','images')
        self.assertEqual(prepared.context['T_MAX'],600)
        self.assertEqual(prepared.context['B_MAX'],180)
        self.assertEqual(prepared.context['image_time_axis'],'bottom_to_top')
        self.assertIn('即使原图未显示刻度',prepared.prompt)
        self.assertIn('有效声呐绘图区',prepared.prompt)
        doc=parse_tracks(json.dumps(result('bearing_time')),prepared.context)
        self.assertIn('默认约定',doc['warnings'][1])
        with self.assertRaisesRegex(ValueError,'缺少已知平台'):
            parse_tracks(json.dumps(result()),prepared.context)

    def test_platform_overrides_default_calibration(self):
        raw={**CONTEXT,'T_MAX':120,'DT':0.5,'B_MIN':10,'B_MAX':150,
             'image_time_axis':'top_to_bottom'}
        prepared=prepare_input(2,json.dumps(raw).encode(),'test','images')
        for field in ('T_MAX','DT','B_MIN','B_MAX','image_time_axis'):
            self.assertEqual(prepared.context[field],raw[field])
        data=result('bearing_time');data['tracks'][0]['points'][0]['x']=5
        with self.assertRaisesRegex(ValueError,'标定范围'):
            parse_tracks(json.dumps(data),prepared.context)

    def test_bad_calibration_rejected(self):
        for field,value in [('DT',0),('DT',True),('B_MIN',181),('B_MAX',float('nan')),
                            ('image_time_axis','arbitrary instruction')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                platform_context(json.dumps({**CONTEXT,field:value}).encode())


if __name__=='__main__':
    unittest.main()
