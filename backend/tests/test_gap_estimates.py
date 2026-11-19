import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.services.localization.batch_inverse import evaluate_path,reference_for
from backend.app.services.localization.gap_estimates import estimate_model_gaps
from backend.app.services.rendering.trajectory import render_document
from backend.app.agents.nodes.inverse import InverseNodes
from backend.app.services.localization.identity_continuity import consolidate_identity_tracks
from backend.app.services.evaluation.evaluation import evaluate


CONTEXT={'T_MAX':600,'platforms':[{'name':'A','start_ll':[30,122],
    'heading_deg':0,'speed_kn':0,'hull_m':[0,0],'tow_m':[-150,0]}]}


def fragment(times,identity='N1',variant=0,hypothesis='A001'):
    model={'model':'cv','parameters':[1000,1000,100,0],'t0':0,'span':600}
    xy=evaluate_path(model['parameters'],times,model['model'],model['t0'],model['span'])
    reference=reference_for(CONTEXT);points=[]
    for time,position in zip(times,xy):
        lat,lon=reference.to_ll(tuple(map(float,position)));points.append({'t':time,'x':lon,'y':lat})
    return {'id':identity,'platform_id':'A','association_hypothesis_id':hypothesis,
            'mirror_variant':variant,'motion_model':'cv','model_parameters':model,'points':points}


class GapEstimateTests(unittest.TestCase):
    def test_same_identity_gap_is_model_only_and_does_not_change_observations(self):
        tracks=[fragment([10,20]),fragment([60,70])];original=copy.deepcopy(tracks)
        gaps=estimate_model_gaps(tracks,CONTEXT)
        self.assertEqual(len(gaps),1)
        self.assertEqual((gaps[0]['start_t'],gaps[0]['end_t']),(20,60))
        self.assertTrue(gaps[0]['estimated_not_observed'])
        self.assertFalse(gaps[0]['used_for_evaluation'])
        self.assertGreater(len(gaps[0]['points']),2)
        self.assertEqual(tracks,original)

    def test_never_connects_other_identity_mirror_or_association(self):
        for kwargs in ({'identity':'N2'},{'variant':1},{'hypothesis':'A002'}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(estimate_model_gaps([fragment([10,20]),fragment([60,70],**kwargs)],CONTEXT),[])

    def test_long_gaps_and_overlapping_times_remain_unconnected(self):
        for times in ([200,210],[15,25]):
            self.assertEqual(estimate_model_gaps([fragment([10,20]),fragment(times)],CONTEXT),[])

    def test_incompatible_or_stale_model_is_not_connected(self):
        left=fragment([10,20]);right=fragment([60,70]);right['model_parameters']['parameters'][0]+=1
        self.assertEqual(estimate_model_gaps([left,right],CONTEXT),[])
        right=fragment([60,70]);right['points'][0]['x']+=.1
        self.assertEqual(estimate_model_gaps([left,right],CONTEXT),[])

    def test_same_identity_is_one_track_in_data_and_one_solid_curve(self):
        tracks=[fragment([10,20]),fragment([60,70])];original=copy.deepcopy(tracks);lines=[]
        merged,report=consolidate_identity_tracks(tracks,CONTEXT)
        self.assertEqual(len(merged),1)
        self.assertEqual(report['merged_fragment_count'],1)
        self.assertEqual(tracks,original)
        self.assertEqual(len(merged[0]['observation_segments']),2)
        self.assertEqual(len(merged[0]['inferred_intervals']),1)
        self.assertTrue(any(not p['observation_supported'] for p in merged[0]['points']))
        doc={'coordinate_system':'lon_lat','result_source':'numeric_forward_inverse',
             'plot_kind':'spatial_candidates','warnings':[],'tracks':merged,'identity_continuity':report}
        def capture(fig,*args,**kwargs):
            lines.extend((line.get_label(),line.get_linestyle()) for line in fig.axes[0].lines if len(line.get_xdata())>1)
        with tempfile.TemporaryDirectory() as folder,patch('matplotlib.figure.Figure.savefig',autospec=True,side_effect=capture):
            render_document(doc,folder,CONTEXT)
        self.assertEqual(len(lines),1)
        self.assertEqual(lines[0][1],'-')
        self.assertNotIn('model_gap_estimates',doc)
        self.assertEqual(doc['plot_summary']['identity_hypotheses'],1)

    def test_data_consolidation_refuses_different_identity_mirror_or_hypothesis(self):
        for kwargs in ({'identity':'N2'},{'variant':1},{'hypothesis':'A002'}):
            merged,report=consolidate_identity_tracks([fragment([10,20]),fragment([60,70],**kwargs)],CONTEXT)
            self.assertEqual(len(merged),2)
            self.assertEqual(report['merged_fragment_count'],0)

    def test_structured_evaluation_still_excludes_inferred_times(self):
        tracks=[fragment([10,20]),fragment([60,70])]
        predicted=fragment([10,20,40,60,70])
        truth='\n'.join(json.dumps({'platform_id':'A','target_id':'T1','time_s':p['t'],'lon':p['x'],'lat':p['y']}) for p in predicted['points']).encode()
        old={'coordinate_system':'lon_lat','tracks':tracks}
        merged,report=consolidate_identity_tracks(tracks,CONTEXT)
        new={'coordinate_system':'lon_lat','tracks':merged}
        before=evaluate(old,truth_raw=truth);after=evaluate(new,truth_raw=truth)
        self.assertEqual(before,after)
        self.assertEqual(after['position_metrics']['overall_time_coverage_percent'],80.)

    def test_consolidation_is_idempotent_and_does_not_promote_predictions_to_observations(self):
        tracks=[fragment([10,20]),fragment([60,70])]
        first,report=consolidate_identity_tracks(tracks,CONTEXT)
        second,again=consolidate_identity_tracks(first,CONTEXT)
        self.assertEqual(first,second)
        self.assertEqual(report,again)

    def test_primary_view_does_not_draw_other_mirror_gaps(self):
        tracks=[fragment([10,20]),fragment([60,70]),fragment([10,20],variant=1),fragment([60,70],variant=1)]
        self.assertEqual(len(estimate_model_gaps(tracks,CONTEXT)),2)
        primary=[track for track in tracks if track['mirror_variant']==0]
        gaps=estimate_model_gaps(primary,CONTEXT)
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['mirror_variant'],0)

    def test_agent_persists_gap_predictions_separately_for_all_mirrors(self):
        tracks=[fragment([10,20]),fragment([60,70]),fragment([10,20],variant=1),fragment([60,70],variant=1)]
        document={'coordinate_system':'lon_lat','result_source':'numeric_forward_inverse',
                  'plot_kind':'spatial_candidates','warnings':[],'tracks':copy.deepcopy(tracks)}
        document['tracks'],document['identity_continuity']=consolidate_identity_tracks(document['tracks'],CONTEXT)
        state={'selection':{},'evidence':{},'quality':{},'profile_report':{'status':'completed'}}
        with tempfile.TemporaryDirectory() as folder:
            agent=SimpleNamespace(state={'context':CONTEXT,'paths':[]},control=None,
                new_model_calls=0,review_report={'status':'not_requested'},directory=Path(folder))
            agent.save=lambda name,value:(agent.directory/name).write_text(json.dumps(value),encoding='utf-8')
            with patch('backend.app.agents.nodes.inverse.build_document',return_value=document),\
                    patch('matplotlib.figure.Figure.savefig'):
                InverseNodes(agent).render(state)
            saved=json.loads((agent.directory/'tracks.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['tracks']),2)
        self.assertEqual({t['mirror_variant'] for t in saved['tracks']},{0,1})
        self.assertEqual(saved['identity_continuity']['merged_fragment_count'],2)
        self.assertEqual([s['points'] for t in saved['tracks'] for s in t['observation_segments']],[t['points'] for t in tracks])
        self.assertNotIn('model_gap_estimates',saved)
