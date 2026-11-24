import copy
import unittest
from backend.app.services.rendering.model_continuity import continuous_model_paths


def track(times,identity='N1',variant=0,offset=0.):
    return {'id':identity,'mirror_variant':variant,'branch':f'alternative{variant}:segment{times[0]}',
            'points':[{'t':float(t),'x':float(2*t+offset),'y':float(t)} for t in times]}


def side(t):return ('source_variant',t['mirror_variant'])


class ModelContinuityTests(unittest.TestCase):
    def test_108_second_gap_becomes_one_path_without_new_measurements(self):
        tracks=[track([12,40,93]),track([201,300,588])];original=copy.deepcopy(tracks)
        result,report=continuous_model_paths(tracks,side)
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['points']),6)
        self.assertEqual(result[0]['render_gap_links'][0],[tracks[0]['points'][-1],tracks[1]['points'][0]])
        self.assertEqual(report['added_measurement_points'],0)
        self.assertEqual(tracks,original)

    def test_other_identity_side_overlap_long_gap_and_motion_jump_remain_separate(self):
        for second in (track([60,70],identity='N2'),track([60,70],variant=1),
                       track([15,25]),track([200,210]),track([60,70],offset=5000)):
            with self.subTest(second=second):
                output,report=continuous_model_paths([track([10,20]),second],side)
                self.assertEqual(len(output),2)
                self.assertEqual(report['merged_fragment_count'],0)

    def test_three_compatible_fragments_are_one_path(self):
        output,report=continuous_model_paths([track([10,20]),track([60,70]),track([110,120])],side)
        self.assertEqual(len(output),1)
        self.assertEqual(len(output[0]['render_gap_links']),2)
        self.assertEqual(report['merged_fragment_count'],2)

    def test_unknown_side_is_not_inferred_from_identity_alone(self):
        output,report=continuous_model_paths([track([10,20]),track([60,70])],lambda t:('model_branch',0))
        self.assertEqual(len(output),2)
