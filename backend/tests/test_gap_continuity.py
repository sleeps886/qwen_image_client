import copy
import unittest

from backend.app.services.association.gap_continuity import scan_gaps,review_gaps
from backend.app.services.association.ridge_hypotheses import propose_pairs
from backend.app.services.jobs.inversion_control import InversionControl,InversionStopped


def fragment(name,times,offset=20.,slope=.1,image=0):
    points=[{'id':f'{name}_{t}','sample_index':int(t/10),'t':float(t),
             'bearing_deg':offset+slope*t,'unfolded_deg':offset+slope*t,
             'sigma_deg':1.,'error_deg':.1,'amplitude':8.,'mixed':False} for t in times]
    return {'id':name,'image_index':image,'array':'hull' if image==0 else 'tow',
            'platform_id':'A','points':points,'start_t':points[0]['t'],'end_t':points[-1]['t'],'support':len(points)}


def fixture(tracks):
    evidence={'images':[{'image_index':i,'candidates':[{'t':float(t)} for t in range(0,601,10)]} for i in (0,1)]}
    return {'tracks':tracks,'crossing_regions':[]},evidence


class GapContinuityTests(unittest.TestCase):
    def review(self,tracks):
        ridges,evidence=fixture(tracks);frozen=copy.deepcopy(ridges)
        split,report=scan_gaps(ridges,evidence,InversionControl())
        result,report=review_gaps(split,report,InversionControl())
        self.assertEqual(ridges,frozen)
        return result,report

    def test_bidirectional_join_retains_only_real_measurements(self):
        a=fragment('a',range(0,101,10));b=fragment('b',range(170,261,10))
        result,report=self.review([a,b])
        self.assertEqual(report['retained_joined_hypotheses'],1)
        joined=result['tracks'][-1]
        self.assertEqual({p['id'] for p in joined['points']},{p['id'] for t in (a,b) for p in t['points']})
        self.assertFalse(any(100<p['t']<170 for p in joined['points']))
        self.assertEqual(report['synthetic_measurements'],0)

    def test_inconsistent_slope_and_position_do_not_join(self):
        _,report=self.review([fragment('a',range(0,101,10)),fragment('b',range(170,261,10),offset=90,slope=-.2)])
        self.assertEqual(report['retained_joined_hypotheses'],0)
        self.assertEqual(report['decisions'][0]['status'],'rejected')

    def test_equal_competitors_remain_ambiguous(self):
        _,report=self.review([fragment('a',range(0,101,10)),fragment('b',range(170,261,10)),fragment('c',range(170,261,10),offset=20.1)])
        self.assertEqual(report['retained_joined_hypotheses'],0)
        self.assertTrue(all(d['status']=='ambiguous' for d in report['decisions']))

    def test_already_linked_gap_is_split_before_review(self):
        a=fragment('a',list(range(0,101,10))+list(range(170,261,10)))
        result,report=self.review([a])
        self.assertEqual(report['split_count'],1)
        self.assertEqual(report['retained_joined_hypotheses'],1)
        self.assertEqual(len(result['tracks']),3)

    def test_long_gap_and_cross_array_links_are_not_proposed(self):
        _,report=self.review([fragment('a',range(0,101,10)),fragment('b',range(300,401,10)),fragment('c',range(170,261,10),image=1)])
        self.assertFalse(report['candidate_pairs'])

    def test_folded_bearing_can_continue_through_180(self):
        a=fragment('a',range(0,101,10),offset=165.)
        b=fragment('b',range(170,261,10),offset=165.)
        for track in (a,b):
            for point in track['points']:
                value=point['bearing_deg']%360
                point['bearing_deg']=min(value,360-value)
        _,report=self.review([a,b])
        self.assertEqual(report['retained_joined_hypotheses'],1)

    def test_three_fragments_form_observation_only_chain(self):
        tracks=[fragment('a',range(0,101,10)),fragment('b',range(170,261,10)),fragment('c',range(330,421,10))]
        result,report=self.review(tracks)
        chain=next(t for t in result['tracks'] if t['id']=='a+b+c')
        self.assertEqual(len(chain['points']),sum(len(t['points']) for t in tracks))
        self.assertEqual(chain['missing_intervals'],[[100.,170.],[260.,330.]])

    def test_isolated_endpoint_outlier_does_not_control_prediction(self):
        a=fragment('a',range(0,101,10));b=fragment('b',range(170,261,10))
        a['points'][-1]['bearing_deg']+=8.
        _,report=self.review([a,b])
        self.assertEqual(report['retained_joined_hypotheses'],1)

    def test_no_gap_and_cancellation(self):
        ridges,evidence=fixture([fragment('a',range(0,301,10))])
        _,report=scan_gaps(ridges,evidence,InversionControl())
        self.assertEqual(report['status'],'no_candidate_gaps')
        control=InversionControl();control.cancelled.set()
        with self.assertRaises(InversionStopped):scan_gaps(ridges,evidence,control)

    def test_gap_review_does_not_disable_adjacent_ridge_continuations(self):
        a=fragment('a',range(0,101,10));b=fragment('b',range(110,211,10))
        tow=fragment('tow',range(0,211,10),image=1)
        ridges={'tracks':[a,b,tow],'gap_review_applied':True}
        frozen=copy.deepcopy(ridges)
        result=propose_pairs(ridges,InversionControl())
        pair=next(p for p in result['pairs'] if p['hull']['id']=='a+b')
        self.assertEqual(len(pair['common_rows']),22)
        self.assertEqual(pair['hull']['adjacent_fragment_ids'],['a','b'])
        audit=next(t for t in result['adjacent_continuations'] if t['id']=='a+b')
        self.assertEqual(audit['synthetic_measurements'],0)
        self.assertEqual(audit['observed_measurements'],22)
        self.assertEqual({p['id'] for p in pair['hull']['points']},{p['id'] for t in (a,b) for p in t['points']})
        self.assertEqual(ridges,frozen)

    def test_adjacent_join_does_not_bypass_reviewed_long_gap_rejection(self):
        a=fragment('a',range(0,101,10));b=fragment('b',range(130,231,10))
        tow=fragment('tow',range(0,231,10),image=1)
        result=propose_pairs({'tracks':[a,b,tow],'gap_review_applied':True},InversionControl())
        self.assertFalse(any(p['hull']['id']=='a+b' for p in result['pairs']))

    def test_graph_conditional_routes_execute_gap_node_only_when_needed(self):
        from backend.app.agents.graphs.inverse import build_inverse_graph
        class Agent:
            finalizer=None
            def __init__(self,has_gap):self.has_gap=has_gap;self.called=[];self.events=[]
            def emit(self,event):self.events.append(event)
            def observed(self,name,function):
                def call(state):
                    self.called.append(name)
                    return {'gap_report':{'candidate_pairs':[['a','b']] if self.has_gap else []}} if name=='inverse_paths' else {}
                return call
        for has_gap in (False,True):
            agent=Agent(has_gap);build_inverse_graph(agent).compile().invoke({})
            self.assertEqual('inverse_gap' in agent.called,has_gap)
            self.assertIn('inverse_association',agent.called)
            if not has_gap:self.assertEqual(agent.events[0]['status'],'skipped')
