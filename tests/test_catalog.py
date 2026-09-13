import builtins
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog, norm


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base/'source'
        self.root.mkdir()
        self.db = Catalog(self.base/'data'/'catalog.db')
        self.db.add_root(self.root)
        self.root_row = self.db.roots()[0]

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        return self.db.scan(self.root_row)

    def test_metadata_scan_never_reads_content_and_unchanged_thumb_survives(self):
        p = self.root/'photo.jpg'
        p.write_bytes(b'not even a real image')
        with patch.object(builtins,'open',side_effect=AssertionError('source content read')):
            self.scan()
        row = self.db.rows()[0]
        self.assertTrue(self.db.save_thumb(row,b'thumbnail'))
        self.scan()
        self.assertIsNone(self.db.next_job())
        self.assertEqual(self.db.thumbnail(row['id']),b'thumbnail')

    def test_update_invalidates_but_keeps_previous_thumbnail(self):
        p = self.root/'photo.jpg'
        p.write_bytes(b'old')
        self.scan()
        old = self.db.rows()[0]
        self.db.save_thumb(old,b'old thumbnail')
        p.write_bytes(b'new content')
        self.scan()
        self.assertFalse(self.db.save_thumb(old,b'stale result'))
        self.assertEqual(self.db.thumbnail(old['id']),b'old thumbnail')
        row = self.db.rows()[0]
        self.assertNotEqual(row['size'],row['thumb_size'])

    def test_candidates_are_not_deleted_and_restore_on_scan(self):
        p = self.root/'photo.jpg'
        p.write_bytes(b'old')
        self.scan()
        row = self.db.rows()[0]
        self.db.save_thumb(row,b'thumb')
        p.unlink()
        self.scan()
        self.assertEqual(self.db.rows()[0]['missing'],1)
        self.assertEqual(self.db.thumbnail(row['id']),b'thumb')
        p.write_bytes(b'back')
        self.scan()
        self.assertEqual(self.db.rows()[0]['missing'],0)

    def test_failed_or_cancelled_scan_cannot_mark_deletions(self):
        p = self.root/'photo.png'
        p.write_bytes(b'x')
        self.scan()
        p.unlink()
        with patch('catalog.os.scandir',side_effect=PermissionError('offline')):
            count, errors = self.scan()
        self.assertTrue(errors)
        self.assertEqual(self.db.rows()[0]['missing'],0)
        self.db.scan(self.root_row,lambda:True)
        self.assertEqual(self.db.rows()[0]['missing'],0)

    def test_partial_subfolder_failure_does_not_mark_root_missing(self):
        sub = self.root/'private'
        sub.mkdir()
        (sub/'a.jpg').write_bytes(b'x')
        self.scan()
        original = os.scandir
        def fail_sub(path):
            if norm(path)==norm(sub):
                raise PermissionError('unavailable')
            return original(path)
        with patch('catalog.os.scandir',side_effect=fail_sub):
            _,errors = self.scan()
        self.assertTrue(errors)
        self.assertEqual(self.db.rows()[0]['missing'],0)

    def test_confirm_removal_checks_reappearance_and_cascades(self):
        p = self.root/'photo.jpg'
        p.write_bytes(b'x')
        self.scan()
        row = self.db.rows()[0]
        self.db.save_thumb(row,b'thumb')
        p.unlink()
        self.scan()
        p.write_bytes(b'back')
        self.assertEqual(self.db.confirm_removal([row['id']])[:2],(0,1))
        self.assertTrue(p.exists())
        p.unlink()
        self.scan()
        self.assertEqual(self.db.confirm_removal([row['id']])[:2],(1,0))
        self.assertEqual(self.db.rows(),[])
        self.assertIsNone(self.db.thumbnail(row['id']))

    def test_confirmation_offline_retains_candidate(self):
        p = self.root/'photo.jpg'
        p.write_bytes(b'x')
        self.scan()
        p.unlink()
        self.scan()
        row = self.db.rows()[0]
        self.root.rename(self.base/'moved')
        removed,restored,errors = self.db.confirm_removal([row['id']])
        self.assertEqual(removed,0)
        self.assertTrue(errors)
        self.assertEqual(len(self.db.rows()),1)

    def test_deleted_parent_can_be_confirmed(self):
        sub = self.root/'sub'
        sub.mkdir()
        p = sub/'photo.jpg'
        p.write_bytes(b'x')
        self.scan()
        p.unlink()
        sub.rmdir()
        self.scan()
        row = self.db.rows()[0]
        self.assertEqual(self.db.confirm_removal([row['id']])[:2],(1,0))

    def test_folder_boundaries_and_wildcards(self):
        for name in ['a','ab','a%_']:
            d = self.root/name
            d.mkdir()
            (d/'photo.jpg').write_bytes(b'x')
        child = self.root/'a'/'child'
        child.mkdir()
        (child/'photo.jpg').write_bytes(b'x')
        self.scan()
        self.assertEqual(len(self.db.rows(self.root/'a')),2)
        self.assertEqual(len(self.db.rows(self.root/'a',recursive=False)),1)
        self.assertEqual(len(self.db.rows(self.root/'a%_')),1)

    def test_existing_coverage_is_idempotent(self):
        child = self.root/'child'
        child.mkdir()
        for p in [self.root,child]:
            self.db.add_root(p)
        self.assertEqual(len(self.db.roots()),1)
        with self.assertRaises(ValueError):
            self.db.add_root(self.base)  # Application storage overlaps.

    def test_symlinks_not_followed(self):
        if os.name=='nt':
            self.skipTest('Windows symlink privileges vary')
        (self.root/'loop').symlink_to(self.root,target_is_directory=True)
        (self.root/'a.jpg').write_bytes(b'x')
        self.scan()
        self.assertEqual(len(self.db.rows()),1)


if __name__=='__main__':
    unittest.main()
