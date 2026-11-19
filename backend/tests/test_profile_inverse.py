import copy
import unittest

import numpy as np

from backend.app.services.localization.batch_inverse import fit_pair,evaluate_path
from backend.app.services.localization.profile_inverse import refine_profiles
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
