import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api

from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed,update_mixed
from backend.tests.test_mixed_prediction import dataset,answer


class DuplicateClaimQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.state=create_mixed(dataset())
        self.ids=[f'A038_{i}' for i in range(5)]
        self.ctx,_=prepare_mixed(self.state,self.ids)

    def check(self,rows):
        raw=answer(self.state,self.ctx,rows)
        result=validate_mixed(self.state,self.ctx,raw)
        occupied=[p['candidate_id'] for t in result['tracks'].values() for p in t['history']]
        self.assertEqual(len(occupied),len(set(occupied)))
        self.assertNotIn('A038_2',occupied)
        self.assertIn('A038_2',result['unassigned_ids'])
        self.assertEqual(result['model_answer'],json.loads(raw))
        return result

    def test_both_owners_lose_contested_point_not_other_valid_points(self):
        before=copy.deepcopy(self.state)
        result=self.check([('N1',self.ids[:3]),('N2',self.ids[2:])])
        self.assertEqual({tuple(p['candidate_id'] for p in t['history']) for t in result['tracks'].values()},
                         {('A038_0','A038_1'),('A038_3','A038_4')})
        self.assertEqual(self.state,before)
        saved=update_mixed(self.state,self.ctx,result)
        self.assertEqual(saved['status'],'awaiting_review')
        self.assertEqual(saved['calls'],self.state['calls'])

    def test_row_order_does_not_award_contested_point(self):
        a=self.check([('N1',self.ids[:3]),('N2',self.ids[2:])])
        b=self.check([('N2',self.ids[2:]),('N1',self.ids[:3])])
        histories=lambda result:{tuple(p['candidate_id'] for p in t['history']) for t in result['tracks'].values()}
        self.assertEqual(histories(a),histories(b))

    def test_unknown_reference_not_masked_by_conflict(self):
        raw=json.loads(answer(self.state,self.ctx,[('N1',self.ids[:3]),('N2',self.ids[2:])]))
        raw['tracks'][1]['observation_ids'].append('O99999999')
        with self.assertRaisesRegex(ValueError,'不存在'):validate_mixed(self.state,self.ctx,json.dumps(raw))

    def test_duplicate_within_one_row_still_invalid(self):
        with self.assertRaisesRegex(ValueError,'内部候选重复'):
            validate_mixed(self.state,self.ctx,answer(self.state,self.ctx,[('N1',['A038_0','A038_0'])]))

    def test_targets_losing_all_support_are_not_fabricated(self):
        raw=json.loads(answer(self.state,self.ctx,[('N1',['A038_2']),('N2',['A038_2'])]))
        raw['target_hypotheses']=[{'target_ref':'H1','fragment_refs':['N1','N2'],'pairing_ref':'recompute','reason':'test'}]
        result=validate_mixed(self.state,self.ctx,json.dumps(raw))
        self.assertFalse(result['tracks']);self.assertFalse(result['targets'])
        self.assertTrue(any(w['code']=='target_support_quarantined' for w in result['warnings']))

    def test_saved_reply_recovery_has_no_new_call_and_keeps_raw_reply(self):
        raw=answer(self.state,self.ctx,[('N1',self.ids[:3]),('N2',self.ids[2:])])
        with tempfile.TemporaryDirectory() as root,patch.object(api,'MIXED_ROOT',Path(root)),TestClient(app,base_url='http://127.0.0.1') as client:
            directory=api.ResultRepository(api.MIXED_ROOT).create_run()
            state=copy.deepcopy(self.state);state.update(status='invalid_response',calls=1)
            api.save_state(directory/'state.json',state)
            (directory/'attempt_001.json').write_text(json.dumps({'status':'returned','input':self.ctx,'answer':raw}),encoding='utf-8')
            response=client.post(f'/api/incremental/mixed-sessions/{directory.name}/recover-association',json={'version':0})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['model_calls'],0)
            self.assertEqual(data['calls'],1);self.assertEqual(data['status'],'awaiting_review')
            self.assertEqual(json.loads((directory/'attempt_001.json').read_text(encoding='utf-8'))['answer'],raw)
