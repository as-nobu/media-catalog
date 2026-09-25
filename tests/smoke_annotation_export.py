"""Exercise export UI, pending edits, background execution and compact import."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtCore import QTimer
from app import Window, Service
from catalog import Catalog
from annotation_import import export_annotations, read_source
from import_dialog import ImportDialog

app = QApplication([])
def wait_for(condition):
    deadline = time.monotonic()+10
    while not condition() and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.01)
    assert condition(), 'Timed out waiting for GUI operation'

with tempfile.TemporaryDirectory() as temp:
    base = Path(temp)
    root = base/'files'; root.mkdir(); (root/'a.tif').write_bytes(b'fixture')
    db = Catalog(base/'state'/'catalog.sqlite3')
    db.add_root(root); db.scan(db.roots()[0])
    with patch.object(Service, 'start'):
        window = Window(db)
    gate = threading.Event()
    try:
        window.show()
        wait_for(lambda: bool(window.model.items))
        window.language_combo.setCurrentIndex(1)
        assert window.export_annotations_button.text() == 'Export notes and tags (compact DB)'
        window.language_combo.setCurrentIndex(0)
        assert window.export_annotations_button.text() == 'メモ・タグを書き出す（軽量DB）'
        window.view.setCurrentIndex(window.model.index(0))
        window.details.memo.setPlainText('書き出し直前のメモ')
        target = base/'annotations.sqlite3'
        def delayed(db, path):
            assert gate.wait(10)
            return export_annotations(db, path)
        ticks = []
        timer = QTimer(); timer.timeout.connect(lambda: ticks.append(1)); timer.start(20)
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target),'')), \
             patch('annotation_import.export_annotations', side_effect=delayed), \
             patch.object(QMessageBox, 'information'):
            window.export_annotations()
            assert not window.export_annotations_button.isEnabled()
            assert db.rows()[0]['memo'] == '書き出し直前のメモ'
            window.quit_app()
            assert not window.exiting
            wait_for(lambda: len(ticks)>=5)
            gate.set()
            wait_for(lambda: window.annotation_export_future is None)
            assert window.export_annotations_button.isEnabled()
        timer.stop()
        assert read_source(target)[0][0]['memo'] == '書き出し直前のメモ'
        uid = db.rows()[0]['registration_uid']
        with db.connect() as c:
            c.execute("UPDATE files SET memo='' WHERE registration_uid=?", (uid,))
        dialog = ImportDialog(db, str(target), window)
        assert dialog.table.rowCount() == 1
        assert dialog.incoming.toPlainText() == '書き出し直前のメモ'
        with patch.object(QMessageBox, 'information'):
            dialog.apply()
            wait_for(lambda: not dialog.busy)
        assert db.rows()[0]['memo'] == '書き出し直前のメモ'
        before = target.read_bytes()
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target),'')), \
             patch.object(QMessageBox, 'warning') as warning:
            window.export_annotations()
            wait_for(lambda: window.annotation_export_future is None)
            assert warning.called
        assert target.read_bytes() == before
    finally:
        gate.set()
        window.details.timer.stop(); window.details.save_timer.stop()
        window.stats_timer.stop(); window.refresh_timer.stop(); window.db_poll.stop()
        window.ui_pool.shutdown(wait=True); window.model.pool.shutdown(wait=True)
        window.exiting=True; window.tray.hide(); window.close()
print('Compact annotation export/import GUI smoke passed')
