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


    def test_ai_without_qt_and_corrupt_pdf(self):
        from unittest.mock import patch
        from vector_preview import save_illustrator
        from thumbnail_worker import save_pages
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'no-qt.ai'
            out = Path(temp)/'out.jpg'
            Image.new('RGB',(1000,500),'red').save(source,format='PDF')
            original = source.read_bytes()
            with patch.dict(sys.modules, {'PySide6':None, 'PySide6.QtPdf':None}):
                save_illustrator(source,out,save_pages)
            with Image.open(out) as image:
                self.assertEqual(image.size,(512,256))
                self.assertGreater(image.getpixel((256,128))[0],240)
            self.assertEqual(source.read_bytes(),original)
            source.write_bytes(b'%PDF-1.7\ninvalid')
            with self.assertRaisesRegex(RuntimeError,'AIのPDF互換'):
                save_illustrator(source,out,save_pages)


    def test_password_error_is_reported_without_dialog(self):
        import pypdfium2 as pdfium
        from unittest.mock import patch
        from vector_preview import save_illustrator
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'locked.ai'
            source.write_bytes(b'%PDF-1.7\n')
            error = pdfium.PdfiumError('password', err_code=pdfium.raw.FPDF_ERR_PASSWORD)
            with patch.object(pdfium,'PdfDocument',side_effect=error):
                with self.assertRaisesRegex(RuntimeError,'パスワード付きAI'):
                    save_illustrator(source,Path(temp)/'out.jpg',None)
