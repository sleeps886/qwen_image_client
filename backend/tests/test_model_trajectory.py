import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.agents.forward_inverse_agent import ForwardInverseAgent
from backend.app.agents.model_trajectory import validate_final
from backend.tests.test_full_inverse import inverse_fixture, synthetic_images


class ModelTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.context=inverse_fixture()[0]
        self.candidates={'coordinate_system':'local_xy_m','tracks':[{'id':'C1','points':[{'t':0,'x':0,'y':0},{'t':600,'x':1,'y':1}]}]}
        self.answer={'coordinate_system':'local_xy_m','position_observable':True,'warnings':[],
                     'tracks':[{'id':'T1','source_candidate_ids':['C1'],'points':[{'t':12,'x':321,'y':654},{'t':588,'x':987,'y':432}]}]}

    def test_validation_rejects_invalid_time_source_frame_and_values(self):
        self.assertEqual(validate_final(json.dumps(self.answer),self.context,self.candidates)['tracks'][0]['points'][0]['x'],321)
        for field,value in [('t',700),('x',float('nan')),('y',True)]:
            answer=copy.deepcopy(self.answer);answer['tracks'][0]['points'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_final(json.dumps(answer),self.context,self.candidates)
        for change in ('source','frame','duplicate','empty'):
            answer=copy.deepcopy(self.answer)
            if change=='source':answer['tracks'][0]['source_candidate_ids']=['unknown']
            if change=='frame':answer['coordinate_system']='lon_lat'
            if change=='duplicate':answer['tracks']*=2
            if change=='empty':answer['tracks']=[]
            with self.subTest(change=change),self.assertRaises(ValueError):validate_final(json.dumps(answer),self.context,self.candidates)

    def test_exact_identity_branch_reference_is_accepted_without_changing_points(self):
        candidates=copy.deepcopy(self.candidates);candidates['tracks'][0]['branch']='cv:alternative0:segment0'
        answer=copy.deepcopy(self.answer);answer['tracks'][0]['source_candidate_ids']=['C1|cv:alternative0:segment0']
        document=validate_final(json.dumps(answer),self.context,candidates)
        self.assertEqual(document['tracks'][0]['points'],answer['tracks'][0]['points'])
        for bad in ('C1|cv:alternative9:segment0','unknown|cv:alternative0:segment0','C1|cv'):
            answer['tracks'][0]['source_candidate_ids']=[bad]
            with self.subTest(reference=bad),self.assertRaisesRegex(ValueError,'候选来源'):
                validate_final(json.dumps(answer),self.context,candidates)

    def test_omitted_identity_and_missing_decisions_are_reported_not_silently_filled(self):
        candidates=copy.deepcopy(self.candidates)
        other=copy.deepcopy(candidates['tracks'][0]);other['id']='C2';candidates['tracks'].append(other)
        answer=copy.deepcopy(self.answer)
        answer['candidate_decisions']=[{'candidate_id':'C1','decision':'ambiguous','reason':'weak range information'}]
        doc=validate_final(json.dumps(answer),self.context,candidates)
        self.assertEqual(doc['model_identity_audit']['omitted_source_identity_ids'],['C2'])
        self.assertEqual(doc['model_identity_audit']['unreviewed_identity_ids'],['C2'])
        self.assertEqual(len(doc['tracks']),1)
        self.assertTrue(any('C2' in warning for warning in doc['warnings']))
        answer['candidate_decisions'].append({'candidate_id':'unknown','decision':'reject','reason':'unproven'})
        with self.assertRaises(ValueError):validate_final(json.dumps(answer),self.context,candidates)

    def test_model_coordinate_reprojection_detects_unjustified_spatial_shift(self):
        from backend.app.services.quality.model_final_quality import audit_geometry
        context,pair,truth=inverse_fixture()
        times=sorted(truth)
        doc={'tracks':[{'id':'T1','source_candidate_ids':['C1'],
                       'points':[{'t':time,'x':truth[time][0],'y':truth[time][1]} for time in (times[0],times[-1])]}]}
        evidence={'images':[{'array':array,'candidates':pair[array]['points']} for array in ('hull','tow')]}
        selection={'selected':[{'id':'A001','supported_measurement_ids':[p['id'] for a in ('hull','tow') for p in pair[a]['points']]}]}
        candidates=copy.deepcopy(self.candidates);candidates['tracks'][0]['association_hypothesis_id']='A001'
        report=audit_geometry(doc,candidates,evidence,context,selection)
        self.assertEqual(report['warning_track_count'],0)
        self.assertLess(report['tracks'][0]['rmse_deg'],1e-6)
        for p in doc['tracks'][0]['points']:p['x']+=2000
        report=audit_geometry(doc,candidates,evidence,context,selection)
        self.assertEqual(report['warning_track_count'],1)
        self.assertFalse(report['independent_truth_used'])

    def test_mirror_coverage_never_invents_a_missing_model_branch(self):
        candidates=copy.deepcopy(self.candidates)
        candidates['tracks'][0].update(branch='alt0',mirror_variant=0)
        alternative=copy.deepcopy(candidates['tracks'][0]);alternative.update(branch='alt1',mirror_variant=1)
        candidates['tracks'].append(alternative)
        answer=copy.deepcopy(self.answer);answer['tracks'][0]['source_candidate_ids']=['C1|alt0']
        doc=validate_final(json.dumps(answer),self.context,candidates)
        self.assertEqual(doc['model_identity_audit']['unrepresented_mirror_variants'],[{'candidate_id':'C1','missing_mirror_variants':[1]}])
        self.assertEqual(len(doc['tracks']),1)
        other=copy.deepcopy(answer['tracks'][0]);other['branch']='alt1';other['source_candidate_ids']=['C1|alt1']
        answer['tracks'].append(other)
        doc=validate_final(json.dumps(answer),self.context,candidates)
        self.assertEqual(doc['model_identity_audit']['unrepresented_mirror_variants'],[])
        self.assertEqual([t['mirror_variant'] for t in doc['tracks']],[0,1])

    def test_offline_recovery_creates_new_history_without_modifying_source_or_calling_cloud(self):
        from backend.app.repositories.result_repository import ResultRepository
        from backend.scripts.recover_model_final import recover
        with tempfile.TemporaryDirectory() as tmp:
            repo=ResultRepository(Path(tmp));source=repo.create_run()
            context,pair,truth=inverse_fixture();times=sorted(truth)
            candidates=copy.deepcopy(self.candidates);candidates['tracks'][0].update(association_hypothesis_id='A001',points=[{'t':t,'x':truth[t][0],'y':truth[t][1]} for t in times])
            answer=copy.deepcopy(self.answer);answer['tracks'][0]['points']=[{'t':t,'x':truth[t][0],'y':truth[t][1]} for t in (times[0],times[-1])]
            fields={'images':[{'array':array,'candidates':pair[array]['points']} for array in ('hull','tow')]}
            selection={'selected':[{'id':'A001','supported_measurement_ids':[p['id'] for array in ('hull','tow') for p in pair[array]['points']]}]}
            for name,value in [('model_final_answer.txt',json.dumps(answer)),('calibration_context.json',context),
                               ('backend_tracks.json',candidates),('inverse_fields.json',fields),('inverse_selection.json',selection),('execution_report.json',{'new_model_calls':1,'node_events':[]})]:
                repo.save(source,name,value)
            original={p.name:p.read_bytes() for p in source.iterdir()}
            with patch('backend.app.infrastructure.llm.gateway.ConfiguredModelGateway.invoke',side_effect=AssertionError('no paid retry')):
                result=recover(source.name,Path(tmp))
            self.assertNotEqual(result['result_id'],source.name)
            self.assertEqual(result['new_model_calls'],0)
            self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()},original)
            saved=repo.historical_result(result['result_id'])
            self.assertEqual(saved['document']['tracks'][0]['points'],answer['tracks'][0]['points'])

    def run_fusion(self,root,fail=False,mismatch=False):
        paths=[root/'btr_A_hull.png',root/'btr_A_tow.png'];synthetic_images(self.context,paths)
        calls=[]
        def finalizer(images,prompt,directory):
            self.assertEqual(len(images),6)
            self.assertTrue(all(Path(p).is_file() for p in images))
            manifest=json.loads((directory/'model_final_input.json').read_text(encoding='utf-8'))
            self.assertEqual([m['kind'] for m in manifest['images']],['original_sonar']*2+['gaussian_measurements_overlay']*2+['numeric_primary_candidate','numeric_mirror_alternatives'])
            candidates=json.loads((directory/'backend_tracks.json').read_text(encoding='utf-8'))
            answer=copy.deepcopy(self.answer);answer['tracks'][0]['source_candidate_ids']=[candidates['tracks'][0]['id']]
            answer['tracks'][0]['points']=[{'t':float(t),'x':600.+1.5*t+(2000. if mismatch else 0.),'y':1200.-.3*t} for t in (12,588)]
            calls.append(images)
            return {'answer':'not json' if fail else json.dumps(answer),'usage':{'total_tokens':123}}
        agent=ForwardInverseAgent(finalizer=finalizer)
        result=agent.run(paths,self.context,root/'outputs')
        self.assertEqual(len(calls),1);self.assertEqual(result['new_model_calls'],1)
        self.assertIn('backend_candidate.png',result['downloads'])
        self.assertIn('backend_tracks.json',result['downloads'])
        self.assertEqual(result['usage']['total_tokens'],123)
        from backend.app.repositories.result_repository import ResultRepository
        repo=ResultRepository(root/'outputs')
        self.assertTrue(repo.file(result['result_id'],'model_final_report.json').is_file())
        if fail or mismatch:
            self.assertIsNone(result['trajectory_image']);self.assertNotIn('tracks.json',result['downloads'])
            self.assertEqual(result['model_final']['status'],'failed')
            self.assertFalse((agent.directory/'estimated_track.png').exists())
        else:
            doc=json.loads((agent.directory/'tracks.json').read_text(encoding='utf-8'))
            self.assertEqual(doc['tracks'][0]['points'][0]['x'],618)
            self.assertEqual(doc['result_source'],'model_visual_fusion')
            self.assertEqual(result['model_final']['status'],'generated')
            self.assertEqual(repo.historical_result(result['result_id'])['usage']['total_tokens'],123)
        node=[e for e in result['node_events'] if e['node']=='inverse_model_final'][-1]
        self.assertEqual(node['status'],'failed' if fail or mismatch else 'completed')

    def test_full_fusion_uses_model_coordinates_and_preserves_numeric_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:self.run_fusion(Path(tmp))

    def test_valid_json_with_bad_geometry_is_blocked_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:self.run_fusion(Path(tmp),mismatch=True)

    def test_invalid_model_answer_never_falls_back_to_numeric_final(self):
        with tempfile.TemporaryDirectory() as tmp:self.run_fusion(Path(tmp),fail=True)


if __name__=='__main__':unittest.main()
