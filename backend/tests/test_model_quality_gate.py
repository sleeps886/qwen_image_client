import copy
import unittest
from backend.tests.test_full_inverse import inverse_fixture
from backend.app.services.quality.model_final_quality import audit_geometry,require_geometry,ModelGeometryRejected


class ModelQualityGateTests(unittest.TestCase):
    def fixture(self):
        context,pair,truth=inverse_fixture();times=sorted(truth)
        points=[{'t':float(t),'x':truth[t][0],'y':truth[t][1]} for t in times]
        document={'tracks':[{'id':'N1','source_candidate_ids':['C1'],'points':copy.deepcopy(points)}]}
        candidates={'coordinate_system':'local_xy_m','tracks':[{'id':'C1','association_hypothesis_id':'A1','points':points}]}
        evidence={'images':[{'array':array,'candidates':copy.deepcopy(pair[array]['points'])} for array in ('hull','tow')]}
        selection={'selected':[{'id':'A1','supported_measurement_ids':[p['id'] for im in evidence['images'] for p in im['candidates']]}]}
        return document,candidates,evidence,context,selection

    def test_valid_coordinates_pass_and_shifted_coordinates_are_blocked(self):
        args=self.fixture();report=audit_geometry(*args)
        require_geometry(report);self.assertTrue(report['gate_passed'])
        for p in args[0]['tracks'][0]['points']:p['x']+=2000.
        report=audit_geometry(*args)
        with self.assertRaises(ModelGeometryRejected):require_geometry(report)

    def test_one_bad_array_cannot_hide_behind_combined_average(self):
        args=self.fixture()
        for point in args[2]['images'][1]['candidates']:point['bearing_deg']+=2.
        report=audit_geometry(*args)
        self.assertLess(report['tracks'][0]['rmse_deg'],1.5)
        self.assertEqual(report['tracks'][0]['status'],'observation_mismatch')
        self.assertFalse(report['gate_passed'])

    def test_single_array_and_disjoint_times_fail_closed(self):
        for mode in ('single','disjoint'):
            args=self.fixture()
            if mode=='single':args[2]['images']=args[2]['images'][:1]
            else:
                args[2]['images'][0]['candidates']=args[2]['images'][0]['candidates'][:8]
                args[2]['images'][1]['candidates']=args[2]['images'][1]['candidates'][-8:]
            report=audit_geometry(*args)
            self.assertEqual(report['tracks'][0]['status'],'insufficient_observations')
            self.assertFalse(report['gate_passed'])

    def test_missing_source_support_does_not_match_unrelated_measurements(self):
        args=list(self.fixture());args[-1]={'selected':[]}
        report=audit_geometry(*args)
        self.assertFalse(report['gate_passed'])
        self.assertEqual(report['tracks'][0]['samples'],0)

    def test_unbacked_direction_change_is_rejected_even_with_similar_bearings(self):
        from unittest.mock import patch
        args=self.fixture()
        points=args[0]['tracks'][0]['points'];x,y=points[0]['x'],points[0]['y']
        for p in points:
            dx,dy=p['x']-x,p['y']-y;p['x']=x-dy;p['y']=y+dx
        # Isolate heading gate: identical mocked angular residuals cannot justify rotation.
        with patch('backend.app.services.quality.model_final_quality.observe',return_value={'hull_deg':50.,'tow_deg':50.}):
            for im in args[2]['images']:
                for p in im['candidates']:p['bearing_deg']=50.
            report=audit_geometry(*args)
        self.assertEqual(report['tracks'][0]['status'],'unsupported_heading_change')
        self.assertFalse(report['gate_passed'])

    def test_empty_or_legacy_report_cannot_bypass_gate(self):
        for report in ({},{'warning_track_count':0}):
            with self.assertRaises(ModelGeometryRejected):require_geometry(report)
