import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from catalog import kind_for


class VectorTests(unittest.TestCase):
    def worker(self,source,kind,out):
        return subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'thumbnail_worker.py'),str(source),kind,str(out)],capture_output=True,text=True,timeout=30)

    def test_svg_and_external_link(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'drawing.svg'; out = Path(temp)/'out.jpg'
            data = b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500"><rect width="1000" height="500" fill="red"/></svg>'
            source.write_bytes(data)
            result = self.worker(source,'svg',out)
            self.assertEqual(result.returncode,0,result.stderr)
            with Image.open(out) as image:
                self.assertEqual(image.size,(512,256))
                self.assertGreater(image.getpixel((256,128))[0],240)
            self.assertEqual(source.read_bytes(),data)
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///missing/cloud.png"/></svg>')
            result = self.worker(source,'svg',Path(temp)/'blocked.jpg')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('SVG',result.stderr)
            self.assertFalse((Path(temp)/'blocked.jpg').exists())

    def test_pdf_compatible_ai_and_legacy(self):
        from PySide6.QtGui import QPdfWriter, QPainter, QColor
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'drawing.ai'; out = Path(temp)/'out.jpg'
            writer = QPdfWriter(str(source))
            painter = QPainter(writer)
            for i in range(21):
                if i: writer.newPage()
                painter.fillRect(0,0,writer.width(),writer.height(),QColor('red'))
            painter.end()
            del writer
            original = source.read_bytes()
            result = self.worker(source,'illustrator',out)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest = json.loads(Path(str(out)+'.json').read_text())
            self.assertEqual(manifest['page_count'],21)
            self.assertEqual(manifest['thumbnail_count'],20)
            self.assertEqual(len(list(Path(str(out)+'.pages').glob('*.jpg'))),20)
            self.assertEqual(source.read_bytes(),original)
            source.write_bytes(b'%!PS-Adobe-3.0\nlegacy AI')
            result = self.worker(source,'illustrator',Path(temp)/'legacy.jpg')
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(temp)/'legacy.jpg').exists())

    def test_extensions(self):
        self.assertEqual(kind_for('test.SVG'),'svg')
        self.assertEqual(kind_for('test.AI'),'illustrator')
