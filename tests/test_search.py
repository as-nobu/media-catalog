from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog


class SearchTests(unittest.TestCase):
    def test_combined_fields_exclusions_sort_and_literals(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source'; root.mkdir()
            for name in ('cells one.tif','cells failed.tif','cells two.png','100%_literal.jpg'):
                (root/name).write_bytes(b'x')
            db=Catalog(Path(temp)/'state'/'db.sqlite3')
            db.add_root(root); db.scan(db.roots()[0])
            with db.connect() as c:
                c.execute("UPDATE files SET tags='実験',memo='Straße sample',metadata_json=?",('{"device":"confocal"}',))
                c.execute("UPDATE files SET size=100,mtime_ns=9999999999999999999 WHERE name='cells one.tif'")
            names=lambda **kw:[r['name'] for r in db.rows(**kw)]
            self.assertEqual(names(search='cells tag:実験 -failed ext:tif'),['cells one.tif'])
            self.assertEqual(names(search='name:"cells one"'),['cells one.tif'])
            self.assertEqual(len(names(search='memo:STRASSE meta:confocal')),4)
            self.assertEqual(names(search='100%_'),['100%_literal.jpg'])
            self.assertEqual(names(search="' OR 1=1 --"),[])
            self.assertEqual(names(kind='video'),[])
            self.assertEqual(names(sort='largest')[0],'cells one.tif')
            self.assertEqual(len(names(search='-ext:tif')),2)
