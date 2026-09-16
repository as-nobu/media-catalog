from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog
from annotation_import import read_source,plan_import,apply_import,path_key


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.root=self.base/'sources'; self.root.mkdir()
        for name in ('a.jpg','b.jpg'): (self.root/name).write_bytes(b'original')
        self.db=Catalog(self.base/'state'/'catalog.sqlite3')
        self.db.add_root(self.root); self.db.scan(self.db.roots()[0])

    def tearDown(self): self.temp.cleanup()

    def incoming(self,name='a.jpg',memo='incoming',tags='one, two'):
        return dict(path=str(self.root/name),memo=memo,tags=tags)

    def test_readonly_backup_source_and_import(self):
        row=self.db.rows()[0]
        self.db.save_annotations(row['registration_uid'],'incoming','tag',False,('','',0))
        source=self.base/'source.sqlite3'; self.db.backup(source)
        before=source.read_bytes()
        self.db.save_annotations(row['registration_uid'],'','',False,('incoming','tag',0))
        rows,roots=read_source(source)
        plan,skip=plan_import(self.db,rows)
        count,backup=apply_import(self.db,plan)
        self.assertEqual((count,skip),(1,0)); self.assertTrue(Path(backup).exists())
        self.assertEqual(self.db.rows()[0]['memo'],'incoming')
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual((self.root/'a.jpg').read_bytes(),b'original')
        self.assertEqual(apply_import(self.db,plan_import(self.db,rows)[0]),(0,None))

    def test_mapping_union_and_conflict_choices(self):
        row=self.db.rows()[0]
        self.db.save_annotations(row['registration_uid'],'current','one',True,('','',0))
        source=dict(path='C:\\Other\\Root\\a.jpg',memo='incoming',tags='one, two')
        plan,skip=plan_import(self.db,[source],'c:/other/root',str(self.root))
        self.assertEqual(skip,0); self.assertTrue(plan[0]['conflict'])
        plan[0]['choice']='both'; apply_import(self.db,plan)
        updated=self.db.rows()[0]
        self.assertEqual(updated['memo'],'current\n\n---\nincoming')
        self.assertEqual(updated['tags'],'one, two'); self.assertEqual(updated['favorite'],1)
        plan,_=plan_import(self.db,[source],'c:/other/root',str(self.root)); plan[0]['choice']='both'
        self.assertEqual(apply_import(self.db,plan),(0,None))
        plan[0]['choice']='replace'; apply_import(self.db,plan)
        self.assertEqual(self.db.rows()[0]['memo'],'incoming')

    def test_atomic_conflict_and_no_empty_erase(self):
        rows=[self.incoming(),self.incoming('b.jpg')]
        plan,_=plan_import(self.db,rows)
        with self.db.connect() as c:c.execute("UPDATE files SET memo='new edit' WHERE name='b.jpg'")
        with self.assertRaises(ValueError):apply_import(self.db,plan)
        self.assertEqual(self.db.rows()[0]['memo'],'')
        plan,_=plan_import(self.db,[self.incoming('b.jpg',memo='',tags='')])
        self.assertEqual(apply_import(self.db,plan),(0,None))
        self.assertEqual(self.db.rows()[1]['memo'],'new edit')

    def test_ambiguous_and_unmatched_skipped(self):
        plan,skip=plan_import(self.db,[self.incoming(),self.incoming(),self.incoming('none.jpg')])
        self.assertEqual((plan,skip),([],3))
        self.assertEqual(path_key('C:\\Root\\A.JPG'),path_key('c:/root/a.jpg'))
        with self.assertRaises(ValueError):plan_import(self.db,[],source_root='c:/root')
