import copy
import json
import math
import unittest
from unittest.mock import patch

from backend.app.services.target_count import link_tracklets,count_hypothesis,audit_count,finalize_count
from backend.app.services.spatial_tracks import solve_spatial
from backend.app.workflows.progress import observed
from backend.app.workflows.evidence_graph import build_preflight
from backend.tests.test_spatial_tracks import spatial_fixture


def count_fixture():
    evidence={'images':[]};tracks=[]
    for image_index,array in enumerate(('hull','tow')):
        candidates=[]
        for row in range(8):
            for target,bearing in enumerate((20,60,100,140)):
                candidates.append({'id':f'I{image_index}R{row}P{target}','sample_index':row,
                                   't':row*10,'bearing_deg':bearing+row*.2,'x_px':bearing,'y_px':row})
        evidence['images'].append({'image_index':image_index,'platform_id':'A','array':array,'candidates':candidates})
    for target in range(3):
        points=[{'t':row*10,'hull_candidate_id':f'I0R{row}P{target}','tow_candidate_id':f'I1R{row}P{target}'} for row in range(8)]
        tracks.append({'id':f'T{target+1}','points':points})
    return evidence,{'tracks':tracks}


class TargetCountTests(unittest.TestCase):
    def test_shared_inventory_evidence_excluded_symmetrically_not_first_wins(self):
        evidence,_=count_fixture();fragments=link_tracklets(evidence)
        items=[{'id':target,'status':'ambiguous','evidence_ids':['I0R0P0','I0R1P0']}
               for target in ('T1','T2')]
        for inventory in (items,list(reversed(items))):
            args=(json.dumps({'target_inventory':inventory}),evidence,fragments,
                  count_hypothesis(fragments),{'tracks':[]})
            with self.assertRaises(ValueError):audit_count(*args)
            report=audit_count(*args,isolate_shared_evidence=True)
            self.assertEqual(report['supported_inventory_target_count'],0)
            self.assertEqual(set(report['unconfirmed_inventory_ids']),{'T1','T2'})
            self.assertEqual(len(report['shared_inventory_evidence']),2)
            self.assertTrue(all(not x['validated_evidence_ids'] for x in report['inventory']))
            self.assertFalse(report['unique_target_count_confirmed'])

    def test_shared_inventory_does_not_block_or_mutate_validated_other_tracks(self):
        evidence,observations=count_fixture();observations['tracks']=observations['tracks'][:1]
        original=copy.deepcopy(observations);fragments=link_tracklets(evidence)
        answer=json.dumps({'target_inventory':[
            {'id':'T2','status':'ambiguous','evidence_ids':['I0R0P0','I0R1P0']},
            {'id':'T3','status':'ambiguous','evidence_ids':['I0R0P0','I0R1P0','I0R2P2','I0R3P2']}]})
        report=audit_count(answer,evidence,fragments,count_hypothesis(fragments),observations,
                           isolate_shared_evidence=True)
        self.assertEqual(report['associated_target_count'],1)
        self.assertEqual(report['supported_inventory_target_count'],1)
        self.assertEqual(report['unconfirmed_inventory_ids'],['T2'])
        self.assertEqual(report['inventory'][1]['validated_evidence_ids'],['I0R2P2','I0R3P2'])
        self.assertEqual(observations,original)
        self.assertEqual(report['shared_inventory_evidence'][0]['target_ids'],['T1','T2','T3'])

    def test_model_clutter_is_not_accepted_as_independent_exclusion_evidence(self):
        evidence,observations=count_fixture();fragments=link_tracklets(evidence)
        observations['tracks']=observations['tracks'][:2]
        original=copy.deepcopy(observations)
        answer={'target_inventory':[
            {'id':'T3','status':'clutter','evidence_ids':['I0R0P2','I0R1P2'],
             'reason':'弱直线且局部方位抖动，猜测多径'},
            {'id':'T4','status':'clutter','evidence_ids':['I0R0P3','I0R1P3'],
             'reason':'猜测固定障碍物'}]}
        report=audit_count(json.dumps(answer),evidence,fragments,count_hypothesis(fragments),observations)
        self.assertEqual(report['inventory_target_count'],4)
        self.assertEqual(report['associated_target_count'],2)
        self.assertEqual([i['status'] for i in report['inventory']],['ambiguous','ambiguous'])
        self.assertTrue(all(i['model_status']=='clutter' for i in report['inventory']))
        self.assertEqual([x['id'] for x in report['clutter_classification_reviews']],['T3','T4'])
        self.assertTrue(all(x['cross_time_evidence'] for x in report['clutter_classification_reviews']))
        self.assertEqual(observations,original)  # Never invent tracks to fill counts.
        final=finalize_count(report,{'tracks':[{'id':'H1'}]},
            {'continuous_identity_count':1,'identities':[{'id':'H1','model_ids':['T1'],
             'paired_observations':8}],'warnings':[]})
        self.assertEqual(final['unpaired_inventory_ids'],['T3','T4'])
        self.assertEqual(final['inventory_target_count'],4)
        self.assertEqual(final['localized_target_count'],1)
        self.assertFalse(final['unique_target_count_confirmed'])

    def test_unverified_clutter_single_time_still_has_no_temporal_support(self):
        evidence,observations=count_fixture();fragments=link_tracklets(evidence)
        answer={'target_inventory':[{'id':'T4','status':'clutter',
                                    'evidence_ids':['I0R0P3','I1R0P3'],'reason':'weak'}]}
        report=audit_count(json.dumps(answer),evidence,fragments,count_hypothesis(fragments),observations)
        self.assertIn('T4',report['unconfirmed_inventory_ids'])
        self.assertFalse(report['clutter_classification_reviews'][0]['cross_time_evidence'])
        self.assertEqual(report['supported_inventory_target_count'],0)

    def test_clutter_rule_is_actually_retrieved_and_prompt_disallows_unfounded_causes(self):
        from backend.app.services.knowledge import retrieve_rules
        from backend.app.agents.sonar_agent import build_association_prompt
        self.assertIn('clutter-review-v1',[r['id'] for r in retrieve_rules('observation')])
        prompt=build_association_prompt({'images':[]},[])
        self.assertIn('排除证据约束',prompt)
        self.assertIn('不是仰角',prompt)

    def test_four_persistent_traces_not_eight_arrays_or_fragments(self):
        evidence,_=count_fixture();tracklets=link_tracklets(evidence);hypothesis=count_hypothesis(tracklets)
        self.assertEqual(len(tracklets),8)
        self.assertEqual((hypothesis['min'],hypothesis['max']),(4,4))
        evidence['images'][0]['candidates'].extend([{'id':'noise'+str(i),'sample_index':i,'t':i*10,'bearing_deg':175} for i in range(2)])
        self.assertEqual(len(link_tracklets(evidence)),8)

    def test_missed_fourth_and_unlocalized_ids_are_explicit(self):
        evidence,observations=count_fixture();tracklets=link_tracklets(evidence);hypothesis=count_hypothesis(tracklets)
        report=audit_count('{}',evidence,tracklets,hypothesis,observations)
        self.assertEqual(report['associated_target_count'],3)
        self.assertEqual(report['status'],'needs_review')
        self.assertEqual(len(report['uncovered_tracklets']),2)
        final=finalize_count(report,{'tracks':[{'id':'T3'},{'id':'T3'}]})
        self.assertEqual(final['localized_target_count'],1)
        self.assertEqual(final['unlocalized_ids'],['T1','T2'])

    def test_inventory_single_array_target_not_silently_dropped(self):
        evidence,observations=count_fixture();tracklets=link_tracklets(evidence)
        answer={'target_inventory':[{'id':'T4','status':'unpaired','reason':'仅单阵证据',
                                     'evidence_ids':['I0R0P3','I0R1P3']}]}
        report=audit_count(json.dumps(answer),evidence,tracklets,count_hypothesis(tracklets),observations)
        self.assertEqual(report['inventory_target_count'],4)
        self.assertIn('T4',[t['id'] for t in report['targets']])
        answer['target_inventory'].append({**answer['target_inventory'][0],'id':'T5'})
        with self.assertRaises(ValueError):audit_count(json.dumps(answer),evidence,tracklets,count_hypothesis(tracklets),observations)

    def test_real_node_start_end_failure_and_no_fake_completion(self):
        events=[]
        observed('count_audit',lambda state:{'observations':{'tracks':[]}},events.append)({})
        self.assertEqual([e['status'] for e in events],['running','completed'])
        self.assertGreaterEqual(events[-1]['elapsed_ms'],0)
        def fail(_):raise ValueError('failed')
        with self.assertRaises(ValueError):observed('count_audit',fail,events.append)({})
        self.assertEqual(events[-1]['status'],'failed')

    def test_invalid_inventory_refs_are_excluded_not_replaced_in_partial_mode(self):
        evidence,observations=count_fixture();fragments=link_tracklets(evidence)
        answer=json.dumps({'target_inventory':[{'id':'T1','status':'associated','evidence_ids':['unknown','I0R0P0']}]})
        with self.assertRaises(ValueError):audit_count(answer,evidence,fragments,count_hypothesis(fragments),observations)
        report=audit_count(answer,evidence,fragments,count_hypothesis(fragments),observations,isolate_invalid_refs=True)
        self.assertTrue(report['invalid_inventory_references'])
        self.assertNotIn('unknown',report['inventory'][0]['validated_evidence_ids'])
        self.assertEqual(report['status'],'needs_review')

    def test_audit_keeps_submillisecond_precision_and_execution_scope(self):
        events=[]
        with patch('backend.app.workflows.progress.perf_counter_ns',side_effect=[1000000,1234567]):
            observed('count_hypothesis',lambda s:{'count_hypothesis':{'min':1,'max':2}},events.append)({})
        self.assertEqual(events[-1]['elapsed_ms'],.235)
        self.assertEqual(events[-1]['execution_kind'],'local_heuristic')
        self.assertTrue(events[-1]['execution_only_not_accuracy'])
        self.assertEqual(events[0]['started_at'],events[-1]['started_at'])
        self.assertIn('finished_at',events[-1])

    def test_single_time_inventory_uses_only_existing_same_id_pairs_and_keeps_unknown_unconfirmed(self):
        evidence,observations=count_fixture();tracklets=link_tracklets(evidence)
        answer={'target_inventory':[
            {'id':'T1','status':'associated','evidence_ids':['I0R0P0','I1R0P0']},
            {'id':'T4','status':'ambiguous','evidence_ids':['I0R0P3','I1R0P3']}]}
        report=audit_count(json.dumps(answer),evidence,tracklets,count_hypothesis(tracklets),observations)
        self.assertEqual(report['supplemented_from_existing_pairs'],['T1'])
        self.assertEqual(report['unconfirmed_inventory_ids'],['T4'])
        self.assertEqual(report['supported_inventory_target_count'],1)
        self.assertEqual(report['status'],'needs_review')
        self.assertEqual(report['associated_target_count'],3)

    def test_temporal_fragments_with_new_target_ids_are_flagged_not_silently_counted_as_true_targets(self):
        evidence,observations=count_fixture();tracklets=link_tracklets(evidence)
        original=observations['tracks'][0]
        observations['tracks']=[{'id':'T1','points':original['points'][:4]},
                                {'id':'T2','points':original['points'][4:]}]
        report=audit_count('{}',evidence,tracklets,count_hypothesis(tracklets),observations)
        self.assertTrue(report['identity_conflicts'])
        self.assertFalse(report['unique_target_count_confirmed'])

    def test_small_parallax_requires_measured_error_proxy_not_blind_gate_relaxation(self):
        context,observations=spatial_fixture()
        for p in observations['tracks'][0]['points']:
            p['hull_deg']=math.degrees(math.atan2(5000,3000))
            p['tow_deg']=math.degrees(math.atan2(5000,3150))
            p['hull_error_deg']=p['tow_error_deg']=.2
        with self.assertRaises(ValueError):solve_spatial(observations,context)
        result=solve_spatial(observations,context|{'use_candidate_uncertainty':True})
        self.assertEqual(len(result['tracks']),2)
        self.assertEqual(result['localization']['error_method'],'pixel_resolution_and_local_ridge_proxy')
