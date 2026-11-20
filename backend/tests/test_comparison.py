import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw

from backend.app.services.evaluation.comparison import compare_images,normalized_outline


def drawing(kind):
    image=Image.new('RGB',(500,300),'#0a0f1f');d=ImageDraw.Draw(image)
    if kind=='line':
        d.line([(40,60),(430,240)],fill='#00ffff',width=3)
        d.line([(40,240),(400,60)],fill='#ff00cc',width=3)
    elif kind=='curve':
        d.ellipse((40,40,440,260),outline='#00ffff',width=3)
    raw=io.BytesIO();image.save(raw,format='PNG');return raw.getvalue()


class ComparisonTests(unittest.TestCase):
    @staticmethod
    def tracks(indices=(0,1,2,3),colors=None,legend=False):
        image=Image.new('RGB',(500,300),'#0a0f1f');d=ImageDraw.Draw(image)
        palette=colors or ['#80ff00','#ffaa00','#ff00cc','#00ffff']
        for i in indices:d.line([(40,40+i*65),(440,40+i*65)],fill=palette[i],width=3)
        if legend:
            d.line([(450,15),(465,15)],fill='#ffaa00',width=2)
            d.rectangle((245,20,250,25),fill='#ffff00')
        raw=io.BytesIO();image.save(raw,format='PNG');return raw.getvalue()

    def test_per_track_matching_ignores_palette_and_filters_markers(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            report=compare_images(self.tracks(),self.tracks(colors=['#00ffff','#ff00cc','#ffaa00','#80ff00'],legend=True),folder)
        self.assertEqual(report['reference_track_count'],4)
        self.assertEqual(report['prediction_track_count'],4)
        self.assertEqual(report['similarity_percent'],100)
        self.assertTrue(all(r['status']=='ok' and r['reference_id'][1:]==r['prediction_id'][1:] for r in report['per_track']))

    def test_missing_and_extra_are_zero_score_and_reduce_overall(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            missing=compare_images(self.tracks(),self.tracks(indices=(0,1,3)),folder)
            extra=compare_images(self.tracks(indices=(0,1,3)),self.tracks(),folder)
        self.assertEqual([r['status'] for r in missing['per_track']].count('missing'),1)
        self.assertEqual([r['status'] for r in extra['per_track']].count('extra'),1)
        self.assertEqual(missing['similarity_percent'],75)
        self.assertEqual(extra['similarity_percent'],75)

    def test_single_horizontal_trajectory_can_be_compared(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            report=compare_images(self.tracks(indices=(1,)),self.tracks(indices=(1,)),folder)
        self.assertEqual(report['similarity_percent'],100)
        self.assertEqual(len(report['per_track']),1)

    def test_same_and_different(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            same=compare_images(drawing('line'),drawing('line'),folder)
            self.assertEqual(same['similarity_percent'],100)
            different=compare_images(drawing('line'),drawing('curve'),folder)
            self.assertLess(different['similarity_percent'],50)
            self.assertEqual((Path(folder)/'comparison_overlay.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')

    def test_empty_and_invalid(self):
        for raw in (drawing('empty'),b'not image'):
            with self.assertRaises(ValueError):normalized_outline(raw)


if __name__=='__main__':unittest.main()
