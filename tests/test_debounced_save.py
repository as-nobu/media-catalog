import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from app import DetailsWindow
from catalog import Catalog

class DebouncedSaveTests(unittest.TestCase):
    def test_debounce_and_switch(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); root=p/'source';root.mkdir()
            for name in ('a.jpg','b.jpg'): (root/name).write_bytes(b'x')
            db=Catalog(p/'data'/'db.sqlite3');db.add_root(root);db.scan(db.roots()[0])
            a,b=db.rows()
            d=DetailsWindow(db,a)
            try:
                d.tags.setText('first')
                QTest.qWait(600)
                d.tags.setText('last')
                d.favorite.setChecked(True)
                d.memo.setPlainText('note')
                QTest.qWait(600)
                self.assertEqual(db.file_record(a['registration_uid'])['tags'],'')
                QTest.qWait(500)
                saved=db.file_record(a['registration_uid'])
                self.assertEqual((saved['tags'],saved['memo'],saved['favorite']),('last','note',1))
                d.tags.setText('switch')
                self.assertTrue(d.save())
                d.set_row(b)
                QTest.qWait(1100)
                self.assertEqual(db.file_record(a['registration_uid'])['tags'],'switch')
                self.assertEqual(db.file_record(b['registration_uid'])['tags'],'')
                self.assertFalse(d.save_timer.isActive())
            finally:
                d.timer.stop();d.save_timer.stop();d.deleteLater()
                app.processEvents()
