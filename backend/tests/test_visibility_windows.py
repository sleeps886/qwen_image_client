import copy
import unittest
import numpy as np
from backend.app.services.localization.visibility_windows import extend_visibility_windows
from backend.app.services.localization.batch_inverse import evaluate_path,predicted_bearings,reference_for
from backend.app.services.localization.forward_observation import array_location
from backend.app.services.jobs.inversion_control import InversionControl
from backend.tests.test_full_inverse import inverse_fixture


class VisibilityWindowTests(unittest.TestCase):
    def fixture(self,early_tow=True,duplicate=False):
        context,_,_=inverse_fixture()
        model={'model':'cv','parameters':[1.,2.,.4,-.1],'t0':100.,'span':200.}
        candidate={'id':'C1','start_t':100.,'end_t':180.,'variants':[model],
                   'profile_widths_deg':{'hull':2.,'tow':2.}}
        selected=[candidate]
        if duplicate:
            other=copy.deepcopy(candidate);other['id']='C2';selected.append(other)
        times=np.arange(0.,200.,10.);angles=np.linspace(0,180,361);images=[]
        reference=reference_for(context);platform=context['platforms'][0]
        for array in ('hull','tow'):
            centers=np.array([array_location(platform,platform[array+'_m'],float(t),reference) for t in times])
            bearing=predicted_bearings(evaluate_path(model['parameters'],times,model['model'],model['t0'],model['span']),centers,platform['heading_deg'],reference)
            visible=(times>=20)&(times<=180)
            if array=='tow' and not early_tow:visible&=times>=100
            values=.6+5*visible[:,None]*np.exp(-.5*((angles[None,:]-bearing[:,None])/2)**2)
            images.append({'array':array,'profiles':[{'t':float(t)} for t in times],
                           '_angles':angles,'_profile_values':values})
        return selected,{'images':images},context

    def test_extends_only_with_sustained_both_array_raster_evidence_without_editing_motion(self):
        selected,evidence,context=self.fixture();frozen=copy.deepcopy(selected)
        result,report=extend_visibility_windows(selected,evidence,context,InversionControl())
        self.assertEqual(result[0]['start_t'],20.)
        self.assertEqual(result[0]['end_t'],180.)
        self.assertEqual(result[0]['variants'],frozen[0]['variants'])
        self.assertEqual(selected,frozen)
        self.assertFalse(report['uses_reference_labels'])

    def test_single_array_energy_does_not_extend_visibility(self):
        selected,evidence,context=self.fixture(early_tow=False)
        result,_=extend_visibility_windows(selected,evidence,context,InversionControl())
        self.assertEqual(result[0]['start_t'],100.)

    def test_indistinguishable_identity_cannot_claim_leave_one_out_gain(self):
        selected,evidence,context=self.fixture(duplicate=True)
        result,report=extend_visibility_windows(selected,evidence,context,InversionControl())
        self.assertTrue(all(c['start_t']==100. for c in result))
        self.assertTrue(all(not u['extended'] for u in report['updates']))


if __name__=='__main__':unittest.main()
