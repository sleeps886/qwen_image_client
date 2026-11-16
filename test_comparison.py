import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw

from comparison import compare_images,normalized_outline


def drawing(kind):
    image=Image.new('RGB',(500,300),'#0a0f1f');d=ImageDraw.Draw(image)
    if kind=='line':
        d.line([(40,60),(430,240)],fill='#00ffff',width=3)
        d.line([(40,240),(400,60)],fill='#ff00cc',width=3)
    elif kind=='curve':
        d.ellipse((40,40,440,260),outline='#00ffff',width=3)
    raw=io.BytesIO();image.save(raw,format='PNG');return raw.getvalue()


class ComparisonTests(unittest.TestCase):
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
