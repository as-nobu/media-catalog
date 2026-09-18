import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from thumbnail_worker import generate, export_slides
from catalog import Catalog


class PageLimitTests(unittest.TestCase):
    def test_tiff_boundary_and_total(self):
        with tempfile.TemporaryDirectory() as temp:
            for count in (1,19,20,21,30):
                with self.subTest(count=count):
                    source = Path(temp)/f'{count}.tif'
                    out = Path(temp)/f'{count}.jpg'
                    frames = [Image.new('RGB',(16,16),(i,0,0)) for i in range(count)]
                    frames[0].save(source,save_all=True,append_images=frames[1:])
                    generate(str(source),'image',str(out))
                    manifest = json.loads(Path(str(out)+'.json').read_text())
                    self.assertEqual(manifest['page_count'],count)
                    self.assertEqual(manifest['thumbnail_count'],min(count,20))
                    self.assertEqual(len(list(Path(str(out)+'.pages').glob('*.jpg'))),min(count,20))

    def test_powerpoint_never_exports_page_21(self):
        calls = []
        class Slides:
            Count = 100
            def __call__(self,index):
                if index>20:
                    raise AssertionError('Export beyond page limit')
                return SimpleNamespace(Export=lambda *args:calls.append(index))
        presentation = SimpleNamespace(Slides=Slides(),PageSetup=SimpleNamespace(SlideWidth=960,SlideHeight=540))
        self.assertEqual(len(list(export_slides(presentation,'.'))),20)
        self.assertEqual(calls,list(range(1,21)))

    def test_database_limit_and_existing_cache_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'source'; root.mkdir()
            source = root/'a.tif'; source.write_bytes(b'unchanged source')
            db = Catalog(Path(temp)/'state'/'db.sqlite3')
            db.add_root(root); db.scan(db.roots()[0])
            row = db.rows()[0]
            self.assertTrue(db.save_thumb(row,b'preview',pages=[b'preview']*20,page_count=30))
            self.assertEqual(db.rows()[0]['page_count'],30)
            with db.connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM page_thumbs').fetchone()[0],20)
                c.execute('INSERT INTO page_thumbs VALUES (?,?,?)',(row['id'],21,b'old'))
                c.execute("DELETE FROM settings WHERE key='page_limit_20_v1'")
            db = Catalog(db.path)
            with db.connect() as c:
                self.assertEqual(c.execute('SELECT max(page_number) FROM page_thumbs').fetchone()[0],20)
            self.assertEqual(db.rows()[0]['page_count'],30)
            self.assertEqual(source.read_bytes(),b'unchanged source')


    def test_file_handles_closed_at_limit_and_on_save_error(self):
        from unittest.mock import patch
        from thumbnail_worker import read_pages, save_pages
        real_open = Image.open
        for count in (19,20,21):
            for fail in (False,True):
                with self.subTest(count=count,fail=fail), tempfile.TemporaryDirectory() as temp:
                    source=Path(temp)/'source.tif'
                    frames=[Image.new('RGB',(8,8)) for _ in range(count)]
                    frames[0].save(source,save_all=True,append_images=frames[1:])
                    original=source.read_bytes()
                    handles=[]
                    def tracked_open(*args,**kwargs):
                        im=real_open(*args,**kwargs)
                        handles.append(im.fp)
                        return im
                    pages=read_pages(source)
                    with patch('thumbnail_worker.Image.open',side_effect=tracked_open):
                        if fail:
                            with patch.object(Image.Image,'save',side_effect=OSError('save failed')):
                                with self.assertRaisesRegex(OSError,'save failed'):
                                    save_pages(pages,Path(temp)/'out.jpg')
                        else:
                            save_pages(pages,Path(temp)/'out.jpg')
                    self.assertIsNone(pages.gi_frame)
                    self.assertTrue(handles and all(h.closed for h in handles))
                    self.assertEqual(source.read_bytes(),original)
