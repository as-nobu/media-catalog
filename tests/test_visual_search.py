"""Offline retrieval, incremental-cache and lifecycle regression tests."""
import builtins
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog
from visual_search import SearchIndex, StandardEncoder, SearchCancelled, DinoEncoder


def picture(seed):
    rng = np.random.default_rng(seed)
    image = Image.new('RGB',(512,384),(225,235,245))
    draw = ImageDraw.Draw(image)
    for _ in range(110):
        x,y = rng.integers([0,0],[490,362])
        w,h = rng.integers(5,35,size=2)
        color = tuple(int(v) for v in rng.integers(0,230,size=3))
        draw.ellipse((int(x),int(y),int(x+w),int(y+h)),fill=color,outline='black',width=2)
    for _ in range(12):
        points = tuple(int(v) for v in rng.integers([0,0,0,0],[512,384,512,384]))
        draw.line(points,fill=(40,60,100),width=2)
    draw.text((20,20),'Specimen '+str(seed),fill='black')
    return image


def png(image):
    out = io.BytesIO()
    image.save(out,format='PNG')
    return out.getvalue()


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'cloud'
        self.root.mkdir()
        self.db = Catalog(Path(self.tmp.name)/'local'/'catalog.sqlite3')
        self.db.add_root(self.root)
        self.index = SearchIndex(self.db)

    def add(self, name, images):
        source = self.root/name
        source.write_bytes(b'placeholder')
        self.db.scan(self.db.roots()[0])
        row = next(r for r in self.db.rows() if r['name']==name)
        if images:
            self.db.save_thumb(row,png(images[0]),pages=map(png,images),page_count=len(images))
        return self.db.file_record(row['registration_uid'])

    def test_crop_resize_brightness_no_source_io(self):
        correct = self.add('target.tif',[picture(11),picture(12)])
        for i in range(6):
            self.add('other%d.png'%i,[picture(i+20)])
        self.add('unavailable.jpg',[])
        query = ImageEnhance.Brightness(picture(12).crop((100,60,390,310)).resize((460,397))).enhance(.75)
        real_open = builtins.open
        def guard(path,*args,**kwargs):
            if isinstance(path,(str,os.PathLike)) and str(path).startswith(str(self.root)):
                raise AssertionError('Attempted source access: '+str(path))
            return real_open(path,*args,**kwargs)
        with patch('builtins.open',side_effect=guard), patch('pathlib.Path.open',side_effect=AssertionError('No disk model or source reads')):
            report = self.index.search(png(query))
        self.assertEqual(report.hits[0]['registration_uid'],correct['registration_uid'])
        self.assertEqual(report.hits[0]['page_number'],2)
        self.assertGreaterEqual(report.hits[0]['inliers'],8)
        self.assertEqual(report.without_thumbnail,1)
        self.assertEqual(report.pages,8)
        # Screenshot with a white frame is also usable.
        framed = Image.new('RGB',(512,420),'white'); framed.paste(query,(26,12))
        report = self.index.search(png(framed))
        self.assertEqual(report.hits[0]['registration_uid'],correct['registration_uid'])

    def test_reuse_and_thumbnail_update(self):
        row = self.add('target.png',[picture(2)])
        first = self.index.search(png(picture(2)))
        self.assertEqual(first.indexed,1)
        with patch('visual_search.local_features', wraps=__import__('visual_search').local_features) as extract:
            second = self.index.search(png(picture(2)))
            self.assertEqual(extract.call_count,1)  # query only
        self.assertEqual(second.indexed,0)
        self.db.save_thumb(row,png(picture(3)))
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM visual_features').fetchone()[0],0)
        third = self.index.search(png(picture(3)))
        self.assertEqual(third.indexed,1)
        self.assertEqual(third.hits[0]['revision'],row['revision']+1)

    def test_cancel_resume(self):
        for i in range(4): self.add(str(i)+'.png',[picture(i)])
        stopped = False
        def progress(stage,done,total):
            nonlocal stopped
            stopped = stage=='index' and done==2
        with self.assertRaises(SearchCancelled):
            self.index.build(StandardEncoder(),lambda:stopped,progress)
        self.assertEqual(self.index.build(StandardEncoder()),2)
        self.assertEqual(self.index.build(StandardEncoder()),0)

    def test_filters_and_legacy_thumbnail(self):
        one = self.add('one.jpg',[picture(1)])
        two = self.add('two.tif',[picture(2),picture(3)])
        with self.db.connect() as c: c.execute('DELETE FROM page_thumbs WHERE file_id=?',(one['id'],))
        report = self.index.search(png(picture(3)),allowed_uids=[one['registration_uid']])
        self.assertEqual(report.pages,1)
        self.assertEqual(report.hits[0]['name'],'one.jpg')
        self.assertEqual(report.hits[0]['page_number'],1)
        empty = self.index.search(png(picture(2)),allowed_uids=[])
        self.assertEqual(empty.hits,[])
        self.assertEqual(empty.pages,0)

    def test_corrupt_page_empty_query_and_rebuild(self):
        row = self.add('bad.png',[picture(2)])
        with self.db.connect() as c: c.execute('UPDATE page_thumbs SET data=? WHERE file_id=?',(b'broken',row['id']))
        report = self.index.search(png(picture(2)))
        self.assertEqual(report.errors,1)
        self.assertEqual(report.hits,[])
        self.assertEqual(self.index.build(StandardEncoder()),0)
        with self.db.connect() as c: c.execute('UPDATE page_thumbs SET data=? WHERE file_id=?',(png(picture(2)),row['id']))
        report = self.index.search(png(picture(2)),rebuild=True)
        self.assertEqual(report.errors,0)
        self.assertEqual(len(report.hits),1)
        with self.assertRaises(ValueError): self.index.search(png(Image.new('RGB',(2,2))))

    def test_new_schema_does_not_queue_generation(self):
        row = self.add('done.png',[picture(2)])
        before = self.db.rows()
        with self.db.connect() as c: c.execute('DROP TABLE visual_features')
        other = Catalog(self.db.path)
        self.assertEqual(before,other.rows())
        self.assertIsNone(other.next_job())

    def test_backup_and_cascade(self):
        self.add('target.jpg',[picture(9)])
        self.index.build(StandardEncoder())
        backup = Path(self.tmp.name)/'backup.sqlite3'
        self.db.backup(backup)
        restored = Catalog(backup)
        self.assertEqual(SearchIndex(restored).search(png(picture(9))).indexed,0)
        with self.db.connect() as c:
            c.execute('DELETE FROM files')
            self.assertEqual(c.execute('SELECT count(*) FROM visual_features').fetchone()[0],0)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_racing_generation_rejects_old_revision(self):
        row = self.add('target.jpg',[picture(9)])
        db = self.db
        class UpdatingEncoder(StandardEncoder):
            def encode(self,image,multiple=True):
                db.save_thumb(row,png(picture(10)))
                return super().encode(image,multiple)
        self.index.build(UpdatingEncoder())
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM visual_features').fetchone()[0],0)
        self.assertEqual(self.index.build(StandardEncoder()),1)

    def test_model_failure_does_not_poison_index(self):
        self.add('target.jpg',[picture(9)])
        class BrokenEncoder(StandardEncoder):
            def encode(self,image,multiple=True): raise RuntimeError('model failed')
        with self.assertRaisesRegex(RuntimeError,'model failed'): self.index.build(BrokenEncoder())
        self.assertEqual(self.index.build(StandardEncoder()),1)

    def test_missing_ai_model_does_not_download(self):
        import socket
        with patch.object(socket,'socket',side_effect=AssertionError('network access')):
            with self.assertRaisesRegex(RuntimeError,'DINOv2'):
                DinoEncoder(str(Path(self.tmp.name)/'absent'))

    def test_rotated_and_flat_images(self):
        row = self.add('rotated.png',[picture(4)])
        for i,color in enumerate(('white','black','red')):
            self.add(str(i)+'.png',[Image.new('RGB',(200,200),color)])
        report = self.index.search(png(picture(4).rotate(90,expand=True)))
        self.assertEqual(report.hits[0]['registration_uid'],row['registration_uid'])
        self.assertGreaterEqual(report.hits[0]['inliers'],8)
        report = self.index.search(png(Image.new('RGB',(200,200),'red')))
        self.assertEqual(report.hits[0]['name'],'2.png')

    @unittest.skipUnless(os.environ.get('MEDIA_CATALOG_TEST_DINO'),'optional local DINOv2 model')
    def test_real_dino_offline(self):
        import socket
        correct = self.add('target.jpg',[picture(22)])
        self.add('other.jpg',[picture(23)])
        for path in self.root.iterdir(): path.unlink()
        with patch.object(socket,'socket',side_effect=AssertionError('Network access during AI search')):
            encoder = DinoEncoder(os.environ['MEDIA_CATALOG_TEST_DINO'])
            query = png(picture(22).crop((80,60,400,310)))
            report = self.index.search(query,encoder)
            self.assertEqual(report.hits[0]['registration_uid'],correct['registration_uid'])
            self.assertGreaterEqual(report.hits[0]['inliers'],8)
            self.assertEqual(self.index.search(query,encoder).indexed,0)


if __name__=='__main__': unittest.main()
