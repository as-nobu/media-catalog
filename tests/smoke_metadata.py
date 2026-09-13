"""DB-only detail display and explicit memo save/close behavior."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox
from app import DetailsWindow
from catalog import Catalog

app = QApplication([])
with tempfile.TemporaryDirectory() as temp:
    p = Path(temp)
    (p/'images').mkdir()
    Image.new('RGB',(20,10)).save(p/'images'/'test.png')
    db = Catalog(p/'db'/'catalog.sqlite3')
    db.add_root(p/'images')
    db.scan(db.roots()[0])
    row = db.rows()[0]
    db.save_thumb(row,b'preview',metadata={'幅 (px)':20})
    with patch('builtins.open',side_effect=AssertionError('source I/O')), patch('os.stat',side_effect=AssertionError('source stat')):
        window = DetailsWindow(db,db.rows()[0])
        window.show()
        app.processEvents()
        assert '幅 (px): 20' in window.info.toPlainText()
        window.memo.setPlainText('日本語メモ\n2行目')
        assert window.save()
        assert db.rows(search='2行目')[0]['memo']=='日本語メモ\n2行目'
        window.memo.setPlainText('変更未保存')
        with patch.object(db,'save_annotations',return_value=False), patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):
            assert not window.close()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Save):
            assert window.close()
        assert db.rows()[0]['memo']=='変更未保存'
print('metadata GUI: OK')
