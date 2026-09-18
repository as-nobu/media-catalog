import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog
from thumbnail_worker import generate,check_powerpoint


class EmptyImagesTests(unittest.TestCase):
    def test_empty_image_manifest_database_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sources'; root.mkdir()
            for extension,kind in [('tif','image'),('svg','svg'),('ai','illustrator'),('pptx','powerpoint')]:
                source=root/f'empty.{extension}'; source.touch()
                out=Path(temp)/f'{extension}.jpg'
                generate(str(source),kind,str(out))
                self.assertFalse(out.exists())
                self.assertEqual(json.loads(Path(str(out)+'.json').read_text())['page_count'],0)
                self.assertEqual(source.stat().st_size,0)
            db=Catalog(Path(temp)/'state'/'db.sqlite3')
            db.add_root(root); db.scan(db.roots()[0])
            for row in db.rows():
                db.save_thumb(row,None,pages=[],page_count=0,metadata={'状態':'空ファイル（0 bytes）'})
            self.assertEqual(db.generation_counts()['complete'],4)
            self.assertTrue(all(not r['error'] and r['page_count']==0 for r in db.rows()))
            (root/'empty.tif').write_bytes(b'new content')
            db.scan(db.roots()[0])
            self.assertEqual(db.generation_counts()['pending'],1)

    def test_unknown_proceeds_confirmed_encryption_rejected(self):
        with tempfile.NamedTemporaryFile() as source:
            module=SimpleNamespace(OfficeFile=lambda stream: (_ for _ in ()).throw(ValueError('unknown')))
            with patch.dict(sys.modules,{'msoffcrypto':module}):
                self.assertIsNone(check_powerpoint(source.name))
            module.OfficeFile=lambda stream:SimpleNamespace(is_encrypted=lambda:True)
            with patch.dict(sys.modules,{'msoffcrypto':module}):
                with self.assertRaisesRegex(RuntimeError,'パスワード付き'):
                    check_powerpoint(source.name)

    def test_migration_preserves_previous_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sources'; root.mkdir()
            (root/'empty.jpg').touch()
            (root/'unknown.ppt').write_bytes(b'fixture')
            db=Catalog(Path(temp)/'state'/'db.sqlite3')
            db.add_root(root); db.scan(db.roots()[0])
            with db.connect() as c:
                c.execute("UPDATE files SET error='PPTの暗号化状態を判定できないため、自動生成をスキップしました。',timed_out=1,retry_at=9999999999")
                c.execute("DELETE FROM settings WHERE key='empty_images_unknown_ppt_v1'")
            db=Catalog(db.path)
            self.assertTrue(all(r['error'] for r in db.rows()))
            self.assertIsNone(db.next_job())
