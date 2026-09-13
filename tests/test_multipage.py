import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from thumbnail_worker import generate,export_slides,read_pages,save_pages,fit_page
from catalog import Catalog

COLORS = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255)]


class MultipageTests(unittest.TestCase):
    def assert_color(self,image,expected):
        actual=image.getpixel((image.width//2,image.height//2))
        self.assertTrue(all(abs(a-b)<10 for a,b in zip(actual,expected)),(actual,expected))

    def assert_bundle(self,output,count):
        self.assertEqual(json.loads(Path(str(output)+'.json').read_text())['page_count'],count)
        with Image.open(output) as image:
            self.assert_color(image,COLORS[0])
        for i in range(1,count+1):
            with Image.open(Path(str(output)+'.pages')/f'{i}.jpg') as image:
                self.assertLessEqual(max(image.size),512)
                self.assert_color(image,COLORS[i-1])

    def test_tiff_one_through_five_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            for count in range(1,6):
                with self.subTest(count=count):
                    source=Path(temp)/f'{count}.tiff'
                    output=Path(temp)/f'{count}.jpg'
                    frames=[Image.new('RGB',(1200,800),color) for color in COLORS[:count]]
                    frames[0].save(source,save_all=True,append_images=frames[1:])
                    original=source.read_bytes()
                    generate(str(source),'image',str(output))
                    self.assert_bundle(output,count)
                    with Image.open(output) as image:
                        self.assertEqual(image.width,512)
                    self.assertEqual(source.read_bytes(),original)

    def test_powerpoint_export_one_through_five_slides(self):
        with tempfile.TemporaryDirectory() as temp:
            for count in range(1,6):
                with self.subTest(count=count):
                    calls=[]
                    class Slides:
                        Count=count
                        def __call__(self,index):
                            if index>count:
                                raise IndexError(index)
                            def export(path,fmt,width,height):
                                calls.append(index)
                                Image.new('RGB',(width,height),COLORS[index-1]).save(path)
                            return SimpleNamespace(Export=export)
                    presentation=SimpleNamespace(Slides=Slides(),PageSetup=SimpleNamespace(SlideWidth=960,SlideHeight=540))
                    output=Path(temp)/f'ppt-{count}.jpg'
                    save_pages((next(read_pages(path)) for path in export_slides(presentation,temp)),output)
                    self.assertEqual(calls,list(range(1,count+1)))
                    self.assert_bundle(output,count)

    def test_empty_presentation_errors_cleanly(self):
        with self.assertRaisesRegex(RuntimeError,'スライド'):
            list(export_slides(SimpleNamespace(Slides=SimpleNamespace(Count=0)),'.'))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                save_pages([],Path(temp)/'none.jpg')

    def test_mixed_page_shapes_fit_without_distortion(self):
        for size in [(100,300),(300,100),(1200,800),(800,1200)]:
            output=fit_page(Image.new('RGB',size,'red'),(512,512))
            self.assertLessEqual(max(output.size),512)
            self.assertAlmostEqual(output.width/output.height,size[0]/size[1],delta=.01)

    def test_migration_requeues_all_types_once(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            source=base/'source'; source.mkdir()
            for name in ['a.TIF','b.tiff','c.pptx','d.png']:
                (source/name).write_bytes(b'fixture')
            db=Catalog(base/'state'/'db.sqlite')
            db.add_root(source); db.scan(db.roots()[0])
            for row in db.rows():
                db.save_thumb(row,b'old thumbnail')
            with db.connect() as c:
                c.execute("DELETE FROM settings WHERE key='pages_512_v1'")
                c.execute("UPDATE files SET timed_out=1 WHERE name='b.tiff'")
            db=Catalog(db.path)
            for row in db.rows():
                self.assertIsNone(row['thumb_size'])
                self.assertEqual(db.thumbnail(row['id']),b'old thumbnail')
            self.assertEqual(len(db.rows(timeout_only=True)),1)
            for row in db.rows():
                db.save_thumb(row,b'new thumbnail')
            db=Catalog(db.path)
            self.assertTrue(all(row['thumb_size'] is not None for row in db.rows()))

    def test_page_database_atomic_save_and_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            source=base/'source'; source.mkdir()
            (source/'a.tif').write_bytes(b'fixture')
            db=Catalog(base/'state'/'db.sqlite')
            db.add_root(source); db.scan(db.roots()[0])
            row=db.rows()[0]
            db.save_thumb(row,b'page1',pages=iter([b'page1',b'page2',b'page3']),page_count=3)
            current=db.rows()[0]
            key=(current['id'],current['registration_uid'],current['revision'])
            self.assertEqual(current['page_count'],3)
            self.assertEqual(db.thumbnail(*key),b'page1')
            self.assertEqual(db.page_thumbnail(*key,3),b'page3')
            with self.assertRaises(ValueError):
                db.save_thumb(current,b'bad',pages=iter([b'incomplete']),page_count=4)
            self.assertEqual(db.thumbnail(*key),b'page1')
            self.assertEqual(db.page_thumbnail(*key,3),b'page3')
            db.remove_folder(source)
            self.assertIsNone(db.page_thumbnail(*key,3))
            self.assertFalse(db.save_thumb(row,b'stale',pages=[b'stale'],page_count=1))


if __name__=='__main__':
    unittest.main()
