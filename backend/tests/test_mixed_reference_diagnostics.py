import json
import unittest

from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed
from backend.tests.test_mixed_prediction import dataset


class MixedReferenceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.state=create_mixed(dataset());self.ids=[p['candidate_id'] for p in self.state['points'][:2]]
        self.context,_=prepare_mixed(self.state,self.ids)

    def reply(self,tracks):
        return json.dumps({'step_id':self.context['step_id'],'base_state_version':0,'tracks':tracks})

    def track(self,ref,oid):return {'track_ref':ref,'observation_ids':[oid],'reason':'离线测试'}

    def test_unknown_id_names_exact_protocol_id_and_fragment(self):
        with self.assertRaisesRegex(ValueError,r'O00011.*N3'):
            validate_mixed(self.state,self.context,self.reply([self.track('N3','O00011')]))

    def test_known_but_not_selected_is_distinguished(self):
        unknown=next(alias for cid,alias in self.state['aliases'].items() if cid not in self.ids)
        with self.assertRaisesRegex(ValueError,'未在本轮提交'):
            validate_mixed(self.state,self.context,self.reply([self.track('N1',unknown)]))

    def test_duplicate_ownership_names_both_fragments(self):
        oid=self.context['observations'][0]['observation_id']
        result=validate_mixed(self.state,self.context,self.reply([self.track('N1',oid),self.track('N2',oid)]))
        self.assertFalse(result['tracks'])
        self.assertEqual(result['warnings'][0]['proposed_tracks'],['N1','N2'])
        self.assertEqual(result['warnings'][0]['observation_id'],oid)

    def test_arbitrary_invalid_field_not_echoed(self):
        with self.assertRaises(ValueError) as raised:
            validate_mixed(self.state,self.context,self.reply([self.track('N3','private-untrusted-text')]))
        self.assertNotIn('private-untrusted-text',str(raised.exception))
