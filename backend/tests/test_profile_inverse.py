import copy
import unittest

import numpy as np

from backend.app.services.localization.batch_inverse import fit_pair,evaluate_path
from backend.app.services.localization.profile_inverse import refine_profiles,nuisance_coefficients
from backend.app.services.localization.motion_families import family_seeds
from backend.app.services.localization.observability import range_sensitivity
from backend.app.services.jobs.inversion_control import InversionControl,InversionStopped
from backend.tests.test_full_inverse import inverse_fixture


class ProfileInverseTests(unittest.TestCase):
    def fixture(self):
        context,pair,truth=inverse_fixture()
        fit=fit_pair(pair,context,InversionControl())
        best=min(fit['variants'],key=lambda v:np.mean((evaluate_path(v['parameters'],list(truth),v['model'],v['t0'],v['span'])-np.array(list(truth.values())))**2))
        best=copy.deepcopy(best);best['parameters'][0]+=.1
        fit['variants']=[best,copy.deepcopy(best)]
        fit['supported_measurement_ids']=[p['id'] for a in ('hull','tow') for p in pair[a]['points']]
        angles=np.linspace(0,180,721);images=[]
        for index,array in enumerate(('hull','tow')):
            points=copy.deepcopy(pair[array]['points']);width=3.4 if array=='hull' else 1.5
            for p in points:p['sigma_deg']=width
            profiles=np.array([.6+8*np.exp(-.5*((angles-p['bearing_deg'])/width)**2) for p in points])
            images.append({'array':array,'candidates':points,'_angles':angles,'_profile_values':profiles,
                           'profiles':[{'sample_index':p['sample_index'],'t':p['t']} for p in points]})
        return context,{'selected':[fit]}, {'images':images},truth

    def test_joint_profiles_correct_positions_without_editing_measurements(self):
        context,selection,evidence,truth=self.fixture();before=copy.deepcopy(selection)
        frozen=[copy.deepcopy(im['candidates']) for im in evidence['images']]
        result,report=refine_profiles(selection,evidence,context,InversionControl(),rounds=1,profile_rows=33)
        self.assertEqual(selection,before)
        self.assertEqual(frozen,[im['candidates'] for im in evidence['images']])
        self.assertTrue(any(u['accepted'] for u in report['updates']))
        model=result['selected'][0]['variants'][0]
        xy=evaluate_path(model['parameters'],list(truth),model['model'],model['t0'],model['span'])
        self.assertLess(float(np.sqrt(np.mean(np.sum((xy-np.array(list(truth.values())))**2,axis=1)))),5.)
        self.assertFalse(report['uses_reference_labels'])
        self.assertFalse(report['independent_accuracy_verified'])

    def test_budget_cancel_and_unsupported_geometry(self):
        context,selection,evidence,_=self.fixture()
        with self.assertRaises(ValueError):refine_profiles(selection,evidence,context,InversionControl(),rounds=3)
        control=InversionControl();control.cancelled.set()
        with self.assertRaises(InversionStopped):refine_profiles(selection,evidence,context,control)
        context['platforms'][0]['tow_m'][1]=30
        unchanged,report=refine_profiles(selection,evidence,context,InversionControl())
        self.assertEqual(report['status'],'unsupported_noncollinear_geometry')
        self.assertEqual(unchanged,selection)

    def test_completely_unresolved_profiles_do_not_fake_an_update(self):
        context,selection,evidence,_=self.fixture()
        second=copy.deepcopy(selection['selected'][0]);second['id']='ambiguous-other'
        selection['selected'].append(second)
        result,report=refine_profiles(selection,evidence,context,InversionControl(),rounds=1)
        self.assertTrue(all(not u['accepted'] for u in report['updates']))
        self.assertTrue(all(u['reason']=='insufficient_resolved_pixels' for u in report['updates']))

    def test_phase_deadline_returns_existing_candidates_not_a_fabricated_full_fit(self):
        context,selection,evidence,_=self.fixture()
        result,report=refine_profiles(selection,evidence,context,InversionControl(),phase_seconds=0)
        self.assertEqual(report['status'],'budget_limited')
        self.assertTrue(report['partial_optimization'])
        self.assertFalse(report['updates'])
        self.assertEqual([v['parameters'] for v in result['selected'][0]['variants']],
                         [v['parameters'] for v in selection['selected'][0]['variants']])

    def test_nuisance_fit_keeps_signed_texture_but_nonnegative_target_amplitudes(self):
        design=np.array([[1,-1,0],[1,0,2],[1,1,0],[1,0,2]],dtype=float)
        values=np.array([design@np.array([2.,-1.,1.5]),design@np.array([5.,.2,-1.])])
        gram=np.broadcast_to(design.T@design,(2,3,3)).copy();rhs=values@design
        coefficients=nuisance_coefficients(gram,rhs)
        np.testing.assert_allclose(coefficients[0],[2,-1,1.5],atol=1e-4)
        np.testing.assert_allclose(coefficients[1],[4,.2,0],atol=1e-4)

    def test_motion_family_seeds_ignore_holdout_rows_and_preserve_measurements(self):
        context,selection,evidence,_=self.fixture();candidate=selection['selected'][0]
        frozen=[copy.deepcopy(image['candidates']) for image in evidence['images']];original=copy.deepcopy(candidate);model=candidate['variants'][0]
        check=lambda:None
        before=family_seeds(model,candidate['variants'],candidate,evidence['images'],context,check)
        self.assertEqual(frozen,[image['candidates'] for image in evidence['images']])
        for image in evidence['images']:
            for p in image['candidates']:
                if p['sample_index']%5==0:p['bearing_deg']+=10
        after=family_seeds(model,candidate['variants'],candidate,evidence['images'],context,check)
        summarize=lambda seeds:[(m['model'],m['parameter_family'],m['parameters'],indices.tolist()) for m,indices in seeds]
        self.assertEqual(summarize(before),summarize(after))
        self.assertEqual({m['parameter_family'] for m,_ in before},{'cv','constant_latitude','constant_longitude'})
        self.assertEqual(selection['selected'][0],original)

    def test_harmonic_initial_fit_still_compares_both_axis_families(self):
        context,selection,evidence,_=self.fixture();candidate=selection['selected'][0]
        model={**candidate['variants'][0],'model':'harmonic','parameter_family':'harmonic',
               'parameters':candidate['variants'][0]['parameters']+[0.,0.,0.,0.,.8]}
        seeds=family_seeds(model,[model],candidate,evidence['images'],context,lambda:None)
        self.assertEqual({m['parameter_family'] for m,_ in seeds},{'latitude_harmonic','longitude_harmonic'})
        for model,active in seeds:
            excluded=(4,6) if model['parameter_family']=='latitude_harmonic' else (5,7)
            self.assertTrue(all(model['parameters'][index]==0 for index in excluded))

    def test_single_array_late_peak_does_not_extend_visibility_window(self):
        context,selection,evidence,_=self.fixture();candidate=selection['selected'][0]
        candidate['end_t']=570.
        candidate['supported_measurement_ids'].remove(evidence['images'][0]['candidates'][-1]['id'])
        # No hull raster energy either: missing a clean peak alone no longer
        # proves invisibility when both original raster profiles support it.
        evidence['images'][0]['_profile_values'][-1]=.6
        result,report=refine_profiles(selection,evidence,context,InversionControl(),phase_seconds=0)
        self.assertEqual(report['observation_window_updates'][0]['after'][1],570.)
        self.assertEqual(result['selected'][0]['end_t'],570.)

    def test_tiny_parallax_is_reported_as_weak_range_information(self):
        context,selection,evidence,_=self.fixture();candidate=selection['selected'][0]
        near={**candidate['variants'][0],'model':'cv','parameters':[1.,1.,0.,0.]}
        far={**near,'parameters':[50.,50.,0.,0.]}
        a=range_sensitivity(near,candidate,evidence,context);b=range_sensitivity(far,candidate,evidence,context)
        self.assertGreater(a['median_folded_parallax_deg'],b['median_folded_parallax_deg'])
        self.assertTrue(b['range_sensitive'])
        self.assertFalse(b['is_statistical_confidence'])
