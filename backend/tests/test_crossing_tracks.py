import copy
import unittest

from backend.app.services.crossing_tracks import crossing_continuity
from backend.app.services.identity_association import reconcile_identities
from backend.app.services.target_count import link_tracklets
from backend.app.agents.sonar_agent import build_association_prompt
from backend.app.workflows.evidence_graph import link,assemble
from backend.app.workflows.progress import observed


def crossing_fixture():
    evidence={'images':[]};observations={'tracks':[]}
    for image,array in enumerate(('hull','tow')):
        candidates=[]
        for row in range(17):
            for target in range(2):
                # One blended peak at the crossing: never duplicate it.
                if row==8 and target==1:continue
                angle=(50+4*row if target==0 else 114-4*row)+image*.2
                candidates.append({'id':f'I{image}R{row}P{target}','sample_index':row,
                                   't':row*10,'bearing_deg':angle})
        evidence['images'].append({'image_index':image,'platform_id':'A','array':array,
                                   'roi':[0,0,100,100],'candidates':candidates})
    for target in range(2):
        points=[{'t':r*10,'sample_index':r,'hull_candidate_id':f'I0R{r}P{target}',
                 'tow_candidate_id':f'I1R{r}P{target}',
                 'hull_deg':50+4*r if target==0 else 114-4*r,
                 'tow_deg':(50+4*r if target==0 else 114-4*r)+.2}
                for r in range(17) if r!=8]
        # Simulate the model assigning new names after the gap.
        for name,rows in ((f'T{target+1}',points[:8]),(f'T{target+3}',points[8:])):
            observations['tracks'].append({'id':name,'platform_id':'A','branch':'main','points':rows})
    return evidence,observations


class CrossingTests(unittest.TestCase):
    def test_x_has_two_continuations_without_reusing_crossing_peak(self):
        evidence,observations=crossing_fixture();frozen=copy.deepcopy((evidence,observations))
        report=crossing_continuity(evidence,link_tracklets(evidence))
        self.assertEqual(len(report['events']),2)  # Two arrays, NOT four targets.
        for event in report['events']:
            self.assertEqual(len(event['links']),2)
            self.assertFalse(event['shared_crossing_observation_used'])
            for route in event['links']:
                self.assertEqual(report['sections'][route['before_ids'][0]],
                                 report['sections'][route['after_ids'][-1]])
        result,identity,_=reconcile_identities(observations,evidence)
        self.assertEqual(identity['continuous_identity_count'],2)
        self.assertEqual(len(result['tracks']),4)  # Two identities, each with a gap.
        ids=[p[k] for t in result['tracks'] for p in t['points'] for k in ('hull_candidate_id','tow_candidate_id')]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertFalse(any(p['sample_index']==8 for t in result['tracks'] for p in t['points']))
        self.assertEqual((evidence,observations),frozen)
        self.assertFalse(identity['unique_target_count_confirmed'])

    def test_parallel_lines_or_short_evidence_do_not_force_two_crossing_targets(self):
        evidence,_=crossing_fixture()
        for im in evidence['images']:
            for p in im['candidates']:p['bearing_deg']=30+int(p['id'][-1])*40+p['sample_index']
        self.assertEqual(crossing_continuity(evidence,link_tracklets(evidence))['events'],[])
        for im in evidence['images']:im['candidates']=im['candidates'][:4]
        self.assertEqual(crossing_continuity(evidence,link_tracklets(evidence))['events'],[])

    def test_competing_continuation_is_not_arbitrarily_selected(self):
        evidence,_=crossing_fixture();fragments=link_tracklets(evidence)
        duplicate=copy.deepcopy(fragments[0]);duplicate['id']='competing'
        fragments.append(duplicate)
        report=crossing_continuity(evidence,fragments)
        self.assertFalse(any(e['image_index']==0 for e in report['events']))

    def test_graph_exposes_real_analysis_in_prompt_and_node_event(self):
        evidence,_=crossing_fixture();events=[]
        state={'evidence':evidence,'trace':[],'rules':[]}
        state.update(observed('tracklet_linking',link,events.append)(state))
        self.assertEqual(len(state['crossing_report']['events']),2)
        prompt=assemble(state)['prompt']
        self.assertIn('双侧切线交叉分析',prompt)
        self.assertIn('不能把X整体合成一条',prompt)
        self.assertIn('2处X交叉延续假设',events[-1]['summary'])
        self.assertIn('交叉识别',build_association_prompt(evidence,[]))
