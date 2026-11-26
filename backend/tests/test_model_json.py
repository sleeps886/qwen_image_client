import json
import unittest
from backend.app.services.inputs.model_json import model_object


class ModelJsonTests(unittest.TestCase):
    def setUp(self):
        self.doc={'coordinate_system':'candidate_pairs','tracks':[],'warnings':['引号中的{和}不能切割JSON']}
        self.raw=json.dumps(self.doc,ensure_ascii=False)

    def test_complete_contract_with_prefix_and_fence(self):
        for text in (self.raw,'\ufeff'+self.raw,'```json\n'+self.raw+'\n```',
                     '模型分析文字\n最终结果：\n```json\n'+self.raw+'\n```',
                     '模型分析文字\n'+self.raw+'\n以上为结果'):
            self.assertEqual(model_object(text,'candidate_pairs'),self.doc)

    def test_ambiguous_truncated_or_invalid_contract_is_not_repaired(self):
        for text in ('分析文字，没有JSON','```json\n'+self.raw[:-2]+'\n```',
                     '{"coordinate_system":"candidate_pairs","nested":'+self.raw,
                     self.raw+'\n'+self.raw,'```json\n'+self.raw+'\n```\n```json\n'+self.raw+'\n```'):
            with self.subTest(text=text),self.assertRaises(ValueError):model_object(text,'candidate_pairs')

    def test_wrong_contract_still_reaches_schema_validation_unchanged(self):
        self.assertEqual(model_object('{"coordinate_system":"wrong"}','candidate_pairs')['coordinate_system'],'wrong')

    def test_narrative_with_ellipsis_never_becomes_a_track(self):
        with self.assertRaisesRegex(ValueError,'只返回说明文字'):
            model_object('根据图像分析，选择 I0R0P0 -> ... -> I0R13P0。最终生成JSON仅包含目标T1。','candidate_pairs')
        with self.assertRaisesRegex(ValueError,'没有返回正文'):model_object('','candidate_pairs')

from backend.app.services.inputs.model_json import association_object


class AssociationJsonTests(unittest.TestCase):
    def setUp(self):
        self.doc={'step_id':1,'base_state_version':0,'tracks':[],'ambiguous':[],'unassigned':[]}
        self.raw=json.dumps(self.doc)
    def test_strict_fenced_and_prefixed_object(self):
        for raw in (self.raw,'\ufeff'+self.raw,'```json\n'+self.raw+'\n```','说明\n'+self.raw):
            with self.subTest(raw=raw):self.assertEqual(association_object(raw)[0],self.doc)
    def test_last_explicit_complete_fence_not_first_draft(self):
        draft={**self.doc,'tracks':[{'track_ref':'WRONG'}]}
        text=json.dumps(draft)+'\n说明\n```json\n'+json.dumps(draft)+'\n```\n修订后：\n```json\n'+self.raw+'\n```'
        result,meta=association_object(text)
        self.assertEqual(result,self.doc);self.assertEqual(meta['source'],'terminal_json_fence');self.assertEqual(meta['fenced_blocks'],2)
    def test_ambiguous_unfenced_documents_rejected(self):
        with self.assertRaises(ValueError):association_object(self.raw+'\n'+self.raw)
    def test_never_fallback_to_good_draft_if_final_is_truncated(self):
        with self.assertRaises(ValueError):association_object(self.raw+'\n```json\n'+self.raw[:-3]+'\n```')
    def test_no_nested_fragment_repair(self):
        with self.assertRaises(ValueError):association_object('{"broken":'+self.raw)
    def test_duplicates_nonfinite_and_nonobject_rejected(self):
        for raw in ('{"step_id":1,"step_id":2}','{"value":NaN}','[]'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):association_object(raw)
    def test_wrong_fields_unchanged_for_hard_validation(self):
        self.assertEqual(association_object('```json\n{"tracks":"wrong"}\n```')[0],{'tracks':'wrong'})
