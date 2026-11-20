import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.app.services.rendering.model_views import render_model_views


class ModelViewsTests(unittest.TestCase):
    def test_representative_view_preserves_all_json_branches_and_same_side_fragments(self):
        tracks=[]
        for identity in ('N1','N2'):
            for variant in (1,0):
                for segment in (0,1):
                    branch=f'cv:alternative{variant}:segment{segment}'
                    tracks.append({'id':identity,'branch':branch,'mirror_variant':variant,
                                   'source_candidate_ids':[identity+'|'+branch],
                                   'points':[{'t':segment*20,'x':variant,'y':1},{'t':segment*20+10,'x':variant+1,'y':2}]})
        document={'coordinate_system':'local_xy_m','tracks':tracks,'warnings':[]}
        original=copy.deepcopy(tracks);views=[]
        def render(doc,*args,**kwargs):views.append((kwargs.get('filename','estimated_track.png'),copy.deepcopy(doc)))
        with tempfile.TemporaryDirectory() as tmp,patch('backend.app.services.rendering.model_views.render_document',side_effect=render):
            render_model_views(document,Path(tmp),{},candidates={'tracks':tracks})
            saved=json.loads((Path(tmp)/'tracks.json').read_text(encoding='utf-8'))
        self.assertEqual(len(views[0][1]['tracks']),8)
        self.assertEqual(len(views[1][1]['tracks']),4)
        self.assertTrue(all(t['mirror_variant']==1 for t in views[1][1]['tracks']))
        self.assertEqual(saved['tracks'],original)
        self.assertFalse(saved['primary_display']['side_verified'])
        self.assertEqual(saved['primary_display']['identity_count'],2)


if __name__=='__main__':unittest.main()
