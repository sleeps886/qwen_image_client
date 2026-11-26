import unittest

import numpy as np

from backend.app.services.measurements.btr_measurements import PALETTE
from backend.app.services.quality.inverse_quality import profile_raster


class InversePreviewTests(unittest.TestCase):
    def test_sampled_time_margins_are_not_stretched_to_image_edges(self):
        profiles=[{'t':20.},{'t':50.},{'t':80.}]
        # Distinct, exactly known palette levels at the measured times.
        levels=np.array([80,130,200])
        signal=10**((-5+21*levels/255)/10)
        for axis in ('top_to_bottom','bottom_to_top'):
            pixels=profile_raster(signal[:,None],profiles,101,{'T_MAX':100,'image_time_axis':axis})
            for time,level in zip((20,50,80),levels):
                y=time if axis=='top_to_bottom' else 100-time
                np.testing.assert_array_equal(pixels[y,0],PALETTE[level])
            self.assertFalse(pixels[:20].any())
            self.assertFalse(pixels[81:].any())

    def test_nonuniform_profiles_use_actual_times(self):
        # At t=50 the second sample must stay at row 50, not row 30.
        pixels=profile_raster([[1.],[10.],[1.]],[{'t':20},{'t':50},{'t':90}],101,
                              {'T_MAX':100,'image_time_axis':'top_to_bottom'})
        self.assertEqual(int(np.argmax(pixels[:,0,0])),50)
