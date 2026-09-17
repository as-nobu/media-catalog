from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog
from catalog_restore import prepare_restore,inspect_backup,selected_catalog,select_catalog


class RestoreTests(unittest.TestCase):
    def test_full_restore_relocation_and_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);root=base/'source';root.mkdir();(root/'a.tif').write_bytes(b'original')
            sub=root/'excluded';sub.mkdir()
            db=Catalog(base/'old'/'catalog.sqlite3');db.add_root(root);db.scan(db.roots()[0]);db.remove_folder(sub)
            row=db.rows()[0]
            db.save_annotations(row['registration_uid'],'note','tag',True,('','',0))
            db.save_thumb(row,b'first',pages=[b'first',b'second'],page_count=2,metadata={'test':'value'})
            backup=base/'backup.sqlite3';db.backup(backup);original=backup.read_bytes()
            roots,count=inspect_backup(backup);self.assertEqual(count,1)
            dest=base/'destination';home=base/'new-app'
            restored=prepare_restore(backup,home,{roots[0][0]:str(dest)})
            self.assertEqual(selected_catalog(home),home/'catalog.sqlite3')
            new=Catalog(restored);r=new.rows()[0]
            self.assertEqual(r['path'],str(dest/'a.tif'))
            self.assertEqual((r['memo'],r['tags'],r['favorite']),('note','tag',1))
            self.assertEqual(new.thumbnail(r['id']),b'first')
            self.assertEqual(new.page_thumbnail(r['id'],r['registration_uid'],r['revision'],2),b'second')
            self.assertEqual(new.get_setting('scan_enabled','1'),'0');self.assertEqual(new.get_setting('generate','1'),'0')
            self.assertEqual(new.excluded(),[str(dest/'excluded')])
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(db.rows()[0]['path'],str(root/'a.tif'))
            select_catalog(home,restored);self.assertEqual(selected_catalog(home),Path(restored))
            self.assertEqual((root/'a.tif').read_bytes(),b'original')

    def test_collision_and_invalid_db_do_not_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);db=Catalog(base/'old'/'catalog.sqlite3')
            for name in ('one','two'):
                root=base/name;root.mkdir();db.add_root(root)
            backup=base/'backup.sqlite3';db.backup(backup);home=base/'app'
            roots,_=inspect_backup(backup)
            with self.assertRaises(ValueError):prepare_restore(backup,home,{rid:str(base/'same') for rid,_ in roots})
            self.assertFalse((home/'active-catalog.json').exists())
            invalid=base/'invalid.sqlite3';invalid.write_bytes(b'bad')
            with self.assertRaises(Exception):inspect_backup(invalid)
            self.assertFalse((home/'active-catalog.json').exists())

    def test_windows_paths_on_another_pc(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);db=Catalog(base/'old'/'catalog.sqlite3')
            root=base/'root';root.mkdir();(root/'a.png').write_bytes(b'x');db.add_root(root);db.scan(db.roots()[0])
            with db.connect() as c:
                c.execute("UPDATE roots SET path=?",('C:\\Users\\Other\\Box',))
                c.execute("UPDATE files SET path=?,parent=?",('C:\\Users\\Other\\Box\\a.png','C:\\Users\\Other\\Box'))
                c.execute("UPDATE folders SET path=?",('C:\\Users\\Other\\Box',))
            backup=base/'source.db';db.backup(backup)
            roots,_=inspect_backup(backup)
            new=Catalog(prepare_restore(backup,base/'new',{roots[0][0]:str(base/'local')}))
            self.assertEqual(new.rows()[0]['path'],str(base/'local'/'a.png'))
