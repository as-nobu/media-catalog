import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog,norm


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.parent=self.base/'source'
        self.child=self.parent/'child'
        self.child.mkdir(parents=True)
        self.file=self.child/'a.jpg'
        self.file.write_bytes(b'original')
        self.db=Catalog(self.base/'state'/'db.sqlite')
        self.db.add_root(self.child)
        self.db.scan(self.db.roots()[0])

    def tearDown(self):
        self.temp.cleanup()

    def test_merge_keeps_thumbnails_and_old_scan_cannot_reinsert(self):
        old=self.db.roots()[0]
        row=self.db.rows()[0]
        self.db.save_thumb(row,b'thumb')
        self.db.add_root(self.parent)
        self.assertEqual([r['path'] for r in self.db.roots()],[norm(self.parent)])
        self.db.scan(old)
        self.assertEqual(self.db.rows()[0]['id'],row['id'])
        self.assertEqual(self.db.thumbnail(row['id']),b'thumb')
        self.db.scan(self.db.roots()[0])
        self.assertEqual(len(self.db.rows()),1)

    def test_unregister_subtree_excludes_until_reregistered(self):
        self.db.add_root(self.parent)
        self.assertEqual(self.db.remove_folder(self.child),1)
        self.db.scan(self.db.roots()[0])
        self.assertEqual(self.db.rows(),[])
        self.assertTrue(self.file.exists())
        self.assertNotIn(norm(self.child),self.db.folders())
        self.db.add_root(self.child)
        self.db.scan(self.db.roots()[0])
        self.assertEqual(len(self.db.rows()),1)

    def test_unregister_root_keeps_original_and_stale_scan_is_harmless(self):
        root=self.db.roots()[0]
        row=self.db.rows()[0]
        self.db.save_thumb(row,b'thumb')
        self.db.remove_folder(self.child)
        self.db.scan(root)
        self.assertEqual(self.db.roots(),[])
        self.assertEqual(self.db.rows(),[])
        self.assertIsNone(self.db.thumbnail(row['id']))
        self.assertFalse(self.db.save_thumb(row,b'late result'))
        self.assertEqual(self.file.read_bytes(),b'original')

    def test_empty_folders_are_cataloged(self):
        empty=self.child/'empty'/'nested'
        empty.mkdir(parents=True)
        self.db.scan(self.db.roots()[0])
        self.assertIn(norm(empty),self.db.folders())

    def test_timeout_persists_until_manual_retry(self):
        row=self.db.rows()[0]
        self.db.fail_job(row,'timeout test',timed_out=True)
        with self.db.connect() as c:
            c.execute('UPDATE files SET changed_at=0,retry_at=0')
        reopened=Catalog(self.db.path)
        reopened.scan(reopened.roots()[0])
        self.assertIsNone(reopened.next_job())
        self.assertEqual(len(reopened.rows(timeout_only=True)),1)
        reopened.regenerate([row['id']])
        self.assertIsNotNone(reopened.next_job())
        reopened.save_thumb(row,b'retried')
        self.assertEqual(reopened.rows(timeout_only=True),[])

    def test_unregister_during_scan_does_not_resurrect_records(self):
        entered=threading.Event()
        release=threading.Event()
        original=os.scandir
        def delay(path):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test wait')
            return original(path)
        root=self.db.roots()[0]
        with patch('catalog.os.scandir',side_effect=delay):
            thread=threading.Thread(target=self.db.scan,args=(root,))
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.db.remove_folder(self.child)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.db.rows(),[])


if __name__=='__main__':
    unittest.main()
