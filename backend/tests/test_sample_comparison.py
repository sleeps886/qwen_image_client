from pathlib import Path
import tempfile
import unittest

from backend.scripts.compare_sample import aligned_outline


class SampleComparisonTests(unittest.TestCase):
    def test_shared_frame_does_not_hide_geographic_translation(self):
        labels={'T1':[{'lon':122,'lat':30},{'lon':122.02,'lat':30}]}
        near=[{'points':[{'x':122,'y':30},{'x':122.02,'y':30}]}]
        far=[{'points':[{'x':122,'y':30.01},{'x':122.02,'y':30.01}]}]
        with tempfile.TemporaryDirectory() as root:
            exact=aligned_outline(labels,near,Path(root),'exact.png')
            shifted=aligned_outline(labels,far,Path(root),'shifted.png')
            self.assertAlmostEqual(exact['f1_percent'],100.)
            self.assertAlmostEqual(shifted['f1_percent'],0.)
            self.assertFalse(shifted['independent_recenter_or_scale'])
            self.assertFalse(exact['uses_reference_to_select_side'])
