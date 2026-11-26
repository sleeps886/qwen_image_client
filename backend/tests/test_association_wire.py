import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api

from backend.app.models.association_wire import TASK,FORMAT,REASONS,pack,unpack,encode_context,decode_context,transport_prompt,expand_reply
from backend.app.models.model_settings import ModelSettings
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.association.mixed_prediction import create_mixed,prepare_mixed,validate_mixed,update_mixed,review_mixed
from backend.tests.test_mixed_prediction import dataset


class AssociationWireTests(unittest.TestCase):
    def setUp(self):
        self.state=create_mixed(dataset());self.ids=[p['candidate_id'] for p in self.state['points']]
        self.context,self.prompt=prepare_mixed(self.state,self.ids)

    def test_pack_distinguishes_missing_null_boolean_and_number(self):
        rows=[{'a':None,'b':1,'nested':[True]},{'b':True,'nested':[1]}]
        self.assertEqual(json.dumps(unpack(pack(rows)),sort_keys=True),json.dumps(rows,sort_keys=True))

    def test_transport_roundtrip_no_point_or_field_loss(self):
        original=copy.deepcopy(self.context);wire=encode_context(self.context)
        self.assertEqual(decode_context(wire),self.context)
        self.assertEqual(self.context,original)
        self.assertEqual(len(wire['points']['rows']),len(self.context['observations']))
        text=transport_prompt(self.prompt)
        self.assertEqual(decode_context(json.loads(text.split('\n',1)[1])),self.context)
        self.assertNotIn('PRIVATE',text)

    def test_historical_context_roundtrip_preserves_track_and_review_history(self):
        ctx=self.context;oid=ctx['observations'][0]['observation_id']
        reply={'step_id':1,'base_state_version':0,'tracks':[{'track_ref':'N1','observation_ids':[oid],'reason':'测试'}]}
        pending=update_mixed(self.state,ctx,validate_mixed(self.state,ctx,json.dumps(reply)))
        state=review_mixed(pending,pending['version'],True)
        replay,_=prepare_mixed(state,self.ids,recheck=True)
        self.assertEqual(decode_context(encode_context(replay)),replay)

    def test_explicit_indices_and_reason_codes_validate_without_id_guessing(self):
        result={'format':FORMAT,'step_id':1,'base_state_version':0,
                'tracks':[{'track_ref':'N1','point_indices':[1],'reason_code':'tentative'}],
                'unassigned_indices':[2]}
        expanded,changed=expand_reply(result,self.context)
        self.assertTrue(changed);self.assertEqual(expanded['tracks'][0]['reason'],REASONS['tentative'])
        self.assertEqual(expanded['tracks'][0]['observation_ids'],[self.context['observations'][0]['observation_id']])
        self.assertNotIn('observation_ids',result['tracks'][0],'must not modify raw response')
        checked=validate_mixed(self.state,self.context,json.dumps(result))
        self.assertTrue(checked['reply_format']['explicit_compact_mapping'])
        self.assertEqual(sum(len(t['history']) for t in checked['tracks'].values()),1)

    def test_bad_indices_and_unknown_codes_are_rejected_not_repaired(self):
        for index in [0,-1,True,1.5,99999]:
            with self.subTest(index=index),self.assertRaises(ValueError):
                expand_reply({'tracks':[{'point_indices':[index],'reason_code':'tentative'}]},self.context)
        for row in [{'point_indices':[1],'candidate_ref':'R001'}, {'point_indices':[1],'observation_ids':['O000001']},
                    {'point_indices':[1],'reason_code':'invented'}, {'point_indices':[1],'reason_code':'stable','reason':'extra'}]:
            with self.subTest(row=row),self.assertRaises(ValueError):expand_reply({'tracks':[row]},self.context)

    def test_compact_empty_decisions_keep_all_points_unassigned(self):
        checked=validate_mixed(self.state,self.context,json.dumps({'format':FORMAT,'step_id':1,'base_state_version':0}))
        self.assertFalse(checked['tracks']);self.assertEqual(set(checked['unassigned_ids']),set(self.ids))

    def test_candidate_references_with_codes_remain_exact_and_non_overlapping(self):
        ctx=copy.deepcopy(self.context);ids=[ctx['observations'][0]['observation_id']]
        ctx['multi_time_references']=[{'reference_id':'R001','observation_ids':ids}]
        result={'step_id':1,'base_state_version':0,'tracks':[{'track_ref':'N1','candidate_ref':'R001','reason_code':'tentative'}]}
        checked=validate_mixed(self.state,ctx,json.dumps(result))
        self.assertEqual(sum(len(t['history']) for t in checked['tracks'].values()),1)
        result['tracks'].append({'track_ref':'N2','point_indices':[1],'reason_code':'tentative'})
        checked=validate_mixed(self.state,ctx,json.dumps(result))
        self.assertFalse(checked['tracks'])
        self.assertEqual(checked['warnings'][0]['code'],'duplicate_track_claim')

    def test_gateway_serializes_only_association_not_other_tasks(self):
        gateway=ConfiguredModelGateway(max_prompt_chars=None,association_transport=True)
        settings=ModelSettings(model='offline',base_url='http://invalid.local',api_key='offline')
        with patch('backend.app.infrastructure.llm.gateway.client.send_images',return_value={}) as send:
            gateway.invoke([],self.prompt,settings,Path('.'),'data')
            self.assertEqual(decode_context(json.loads(send.call_args.args[1].split('\n',1)[1])),self.context)
        self.assertEqual(transport_prompt('plain question'),'plain question')
        other='instructions\n'+json.dumps({'task':'model_bearing_plot','observations':[]})
        self.assertEqual(transport_prompt(other),other)

    def test_point_registry_refuses_conflicting_values(self):
        ctx=copy.deepcopy(self.context);p=copy.deepcopy(ctx['observations'][0]);p['bearing_deg']+=1
        ctx['tracks']={'T001':{'history':[p]}}
        with self.assertRaisesRegex(ValueError,'数值不一致'):encode_context(ctx)

    def test_four_point_quadratic_is_marked_weak_not_confirmed(self):
        from backend.app.services.association.tracklet_references import _window_tracklets
        points=[{'candidate_id':str(i),'platform_id':'P','array':'hull','t':i,'bearing_deg':30+2*i*i} for i in range(4)]
        refs=_window_tracklets(points,{str(i):f'O{i+1:06d}' for i in range(4)})
        quadratic=[r for r in refs if r['model_family']=='bearing_time_quadratic']
        self.assertTrue(quadratic)
        for row in quadratic:
            self.assertEqual(row['evidence_level'],'weak_four_point_quadratic')
            self.assertEqual(row['validation'],'in_sample_only')
            self.assertFalse(row['identity_confirmed'])

    def test_real_configured_gateway_compact_stream_validates_offline(self):
        calls=[]
        async def fake_transport(images,prompt,*args,**kwargs):
            wire=json.loads(prompt.split('\n',1)[1]);calls.append(wire)
            context=decode_context(wire)
            text=json.dumps({'format':FORMAT,'step_id':context['step_id'],'base_state_version':context['base_state_version'],
                'tracks':[{'track_ref':'N1','point_indices':[1],'reason_code':'tentative'}]})
            yield {'event':'delta','text':text}
            yield {'event':'model_complete','result':{'answer':text}}
        with tempfile.TemporaryDirectory() as root, patch.object(api,'MIXED_ROOT',Path(root)), \
                patch.object(api,'model_settings',return_value=ModelSettings(model='offline',api_key='offline',base_url='http://invalid.local')), \
                patch('backend.app.infrastructure.llm.gateway.stream_client.stream_images',side_effect=fake_transport):
            client=TestClient(app,base_url='http://127.0.0.1')
            sid=client.post('/api/incremental/mixed-sessions',json={'dataset':dataset()}).json()['session_id']
            url=f'/api/incremental/mixed-sessions/{sid}'
            body={'version':0,'selected_ids':self.ids}
            preview=client.post(url+'/preview',json=body).json()
            self.assertEqual(preview['transport_stats']['observations'],len(self.ids))
            self.assertEqual(preview['model_calls'],0)
            response=client.post(url+'/step?stream=true',json={**body,'consent':True,'context_digest':preview['context_digest']})
            self.assertEqual(response.status_code,200,response.text)
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            self.assertEqual(events[-1]['event'],'result',response.text)
            self.assertEqual(events[-1]['result']['status'],'awaiting_review')
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['wire_format'],'association-table-v1')
            self.assertTrue(events[-1]['result']['rounds'][0]['reply_format']['explicit_compact_mapping'])
