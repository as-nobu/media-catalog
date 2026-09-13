"""Regression tests for reused SQLite IDs and late GUI callbacks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from catalog import Catalog
from app import ThumbnailModel

app = QApplication.instance() or QApplication([])


def picture(color):
    out=io.BytesIO()
    Image.new('RGB',(24,24),color).save(out,'PNG')
    return out.getvalue()


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.a,self.b=self.base/'A',self.base/'B'
        self.a.mkdir(); self.b.mkdir()
        (self.a/'old_red.png').write_bytes(picture('red'))
        (self.b/'new_blue.png').write_bytes(picture('blue'))
        self.db=Catalog(self.base/'state'/'db.sqlite')
        self.model=ThumbnailModel(self.db)

    def tearDown(self):
        self.model.pool.shutdown(wait=True)
        app.processEvents()
        self.temp.cleanup()

    def register(self,folder,color):
        self.db.add_root(folder)
        self.db.scan(self.db.roots()[0])
        row=self.db.rows()[0]
        self.db.save_thumb(row,picture(color))
        return self.db.rows()[0]

    def visible_color(self,row):
        self.model.reset([row])
        key=self.model.key(row)
        deadline=time.monotonic()+3
        while key not in self.model.cache:
            self.model.data(self.model.index(0),Qt.ItemDataRole.DecorationRole)
            app.processEvents()
            if time.monotonic()>deadline:
                self.fail('thumbnail load did not complete')
            time.sleep(.005)
        icon=self.model.data(self.model.index(0),Qt.ItemDataRole.DecorationRole)
        return icon.pixmap(24,24).toImage().pixelColor(12,12).name()

    def test_reused_id_shows_new_image_without_restart(self):
        old=self.register(self.a,'red')
        self.assertEqual(self.visible_color(old),'#ff0000')
        self.db.remove_folder(self.a)
        new=self.register(self.b,'blue')
        self.assertEqual((old['id'],old['revision']),(new['id'],new['revision']))
        self.assertNotEqual(old['registration_uid'],new['registration_uid'])
        self.assertEqual(self.visible_color(new),'#0000ff')
        self.assertEqual(self.model.data(self.model.index(0)),'new_blue.png')

    def test_late_old_callback_is_discarded(self):
        old=self.register(self.a,'red')
        old_key=self.model.key(old)
        self.db.remove_folder(self.a)
        new=self.register(self.b,'blue')
        self.model.reset([new])
        self.model.loaded(old_key,picture('red'))
        self.assertNotIn(old_key,self.model.cache)
        self.assertIsNone(self.db.thumbnail(*old_key))
        self.assertEqual(self.visible_color(new),'#0000ff')

    def test_old_generation_cannot_write_to_same_path_reregistered(self):
        old=self.register(self.a,'red')
        self.db.remove_folder(self.a)
        new=self.register(self.a,'blue')
        self.assertEqual(old['id'],new['id'])
        self.assertFalse(self.db.save_thumb(old,picture('red')))
        self.db.fail_job(old,'stale timeout',timed_out=True)
        self.assertEqual(self.db.rows()[0]['timed_out'],0)
        self.assertEqual(self.visible_color(new),'#0000ff')

    def test_legacy_migration_preserves_thumbnail_and_identity(self):
        old=self.register(self.a,'red')
        with self.db.connect() as c:
            c.execute('DROP INDEX files_registration_uid')
            c.execute('ALTER TABLE files DROP COLUMN registration_uid')
        upgraded=Catalog(self.db.path)
        row=upgraded.rows()[0]
        self.assertEqual(row['id'],old['id'])
        self.assertEqual(upgraded.thumbnail(row['id']),picture('red'))
        uid=row['registration_uid']
        self.assertTrue(uid)
        self.assertEqual(Catalog(self.db.path).rows()[0]['registration_uid'],uid)


if __name__=='__main__':
    unittest.main()
