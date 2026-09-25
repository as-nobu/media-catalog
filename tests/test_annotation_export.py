from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import Catalog
from annotation_import import export_annotations, read_source, plan_import, apply_import
from catalog_restore import inspect_backup


class AnnotationExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'images'
        self.root.mkdir()
        for name in ('a.tif', 'b.tif', 'c.tif'):
            (self.root / name).write_bytes(b'original')
        self.db = Catalog(self.base / 'state' / 'catalog.sqlite3')
        self.db.add_root(self.root)
        self.db.scan(self.db.roots()[0])
        with self.db.connect() as c:
            c.execute("UPDATE files SET memo='日本語\nメモ', tags='細胞, CD31', favorite=1 WHERE name='a.tif'")
            c.execute("UPDATE files SET tags='tag only' WHERE name='b.tif'")
            c.execute('INSERT INTO thumbs SELECT id,zeroblob(2000000) FROM files')
            c.execute("UPDATE files SET metadata_json=?", ('{"large":"' + 'x'*100000 + '"}',))

    def test_compact_roundtrip_and_readonly_source(self):
        target = self.base / 'annotations.sqlite3'
        # No media access is needed, even if the entire source folder is offline.
        for f in self.root.iterdir():
            f.unlink()
        self.root.rmdir()
        queries = []
        original_connect = self.db.connect
        @contextmanager
        def traced():
            with original_connect() as c:
                c.set_trace_callback(queries.append)
                yield c
        with patch.object(self.db, 'connect', side_effect=traced):
            self.assertEqual(export_annotations(self.db, target), 2)
        self.assertFalse(any('thumb' in q.lower() or 'metadata_json' in q.lower() for q in queries))
        self.assertLess(target.stat().st_size, 32768)
        before = target.read_bytes()
        rows, roots = read_source(target)
        self.assertEqual(roots, [str(self.root)])
        self.assertEqual(rows[0]['memo'], '日本語\nメモ')
        with sqlite3.connect(target) as c:
            self.assertEqual({r[1] for r in c.execute('PRAGMA table_info(files)')}, {'path','memo','tags'})
            self.assertFalse(c.execute("SELECT name FROM sqlite_master WHERE name LIKE '%thumb%'").fetchall())
        with self.db.connect() as c:
            c.execute("UPDATE files SET memo='',tags='' WHERE name IN ('a.tif','b.tif')")
        plan, skipped = plan_import(self.db, rows)
        self.assertEqual(skipped, 0)
        self.assertEqual(apply_import(self.db, plan)[0], 2)
        self.assertEqual(self.db.rows()[0]['memo'], '日本語\nメモ')
        self.assertEqual(self.db.rows()[0]['favorite'], 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(apply_import(self.db, plan_import(self.db, rows)[0]), (0, None))
        with self.assertRaises(ValueError):
            inspect_backup(target)

    def test_mapping_conflicts_and_no_overwrite(self):
        target = self.base / 'annotations.sqlite3'
        export_annotations(self.db, target)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            export_annotations(self.db, target)
        self.assertEqual(target.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            export_annotations(self.db, self.db.path)
        rows, _ = read_source(target)
        for row in rows:
            row['path'] = 'C:/other/images/' + Path(row['path']).name
        with self.db.connect() as c:
            c.execute("UPDATE files SET memo='local',tags='local tag' WHERE name='a.tif'")
        plan, skipped = plan_import(self.db, rows, 'C:/other/images', str(self.root))
        self.assertEqual(skipped, 0)
        self.assertTrue(plan[0]['conflict'])
        plan[0]['choice'] = 'both'
        apply_import(self.db, plan)
        self.assertEqual(self.db.rows()[0]['memo'], 'local\n\n---\n日本語\nメモ')
        self.assertEqual(self.db.rows()[0]['tags'], 'local tag, 細胞, CD31')

    def test_empty_and_failure_cleanup(self):
        with self.db.connect() as c:
            c.execute("UPDATE files SET memo='', tags=''")
        target = self.base / 'empty.sqlite3'
        self.assertEqual(export_annotations(self.db, target), 0)
        self.assertEqual(read_source(target)[0], [])
        failed = self.base / 'failed.sqlite3'
        with patch.object(self.db, 'connect', side_effect=OSError('read failed')):
            with self.assertRaises(OSError):
                export_annotations(self.db, failed)
        self.assertFalse(failed.exists())


if __name__ == '__main__':
    unittest.main()
