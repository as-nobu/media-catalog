import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog

class FeaturesTests(unittest.TestCase):
    def test_pause_does_not_start_jobs_until_resumed(self):
        import threading
        from unittest.mock import patch
        from app import Service
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'sources').mkdir()
            (p/'sources'/'a.jpg').write_bytes(b'original')
            db=Catalog(p/'data'/'catalog.sqlite3')
            db.add_root(p/'sources'); db.scan(db.roots()[0])
            with db.connect() as c:
                c.execute('UPDATE files SET changed_at=0')
            started=threading.Event()
            service=Service(db)
            service.paused=True
            with patch.object(service,'scan_all'),patch.object(service,'make_thumbnail',side_effect=lambda job:started.set()):
                service.start()
                try:
                    self.assertFalse(started.wait(.5))
                    service.paused=False
                    service.wake.set()
                    self.assertTrue(started.wait(3))
                finally:
                    service.stop()
                    self.assertTrue(service.wait(5000))

    def test_annotations_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'sources').mkdir()
            (p/'sources'/'a.jpg').write_bytes(b'original')
            db=Catalog(p/'data'/'catalog.sqlite3')
            db.add_root(p/'sources'); db.scan(db.roots()[0])
            row=db.rows()[0]
            self.assertTrue(db.save_annotations(row['registration_uid'],'memo','細胞、 実験,細胞',True,('', '',0)))
            self.assertFalse(db.save_annotations(row['registration_uid'],'stale','',False,('', '',0)))
            self.assertEqual(db.rows(search='実験')[0]['tags'],'細胞, 実験')
            db.save_thumb(row,b'jpeg')
            db.scan(db.roots()[0])
            self.assertEqual(db.rows()[0]['favorite'],1)
            target=p/'backup.sqlite3'
            db.backup(target)
            with sqlite3.connect(target) as c:
                self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok')
                self.assertEqual(c.execute('select memo,tags,favorite from files').fetchone(),('memo','細胞, 実験',1))
                self.assertEqual(c.execute('select data from thumbs').fetchone()[0],b'jpeg')
            with self.assertRaises(FileExistsError): db.backup(target)
            with self.assertRaises(ValueError): db.backup(db.path)
            with self.assertRaises(FileExistsError): db.backup(p/'sources'/'a.jpg')
            self.assertEqual((p/'sources'/'a.jpg').read_bytes(),b'original')
            self.assertEqual(db.generation_counts()['complete'],1)

if __name__=='__main__': unittest.main()
