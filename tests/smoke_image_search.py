"""Headless GUI integration, cached-page navigation and active-search shutdown."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox
from catalog import Catalog
from app import Window
from test_visual_search import picture,png


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)/'cloud'; root.mkdir()
        db = Catalog(Path(tmp)/'local'/'catalog.sqlite3')
        db.set_setting('generate','0'); db.set_setting('scan_enabled','0')
        db.add_root(root)
        for i in range(5): (root/('sample%d.tif'%i)).write_bytes(b'placeholder')
        db.scan(db.roots()[0])
        rows = db.rows()
        for i,row in enumerate(rows):
            db.save_thumb(row,png(picture(i)),[png(picture(i)),png(picture(i+50))],page_count=2)
        for path in root.iterdir(): path.unlink()
        query = QImage.fromData(png(picture(52).crop((70,60,420,320))))
        window = Window(db); window.show()
        stage = 0
        deadline = time.monotonic()+30
        def tick():
            nonlocal stage
            try:
                if window.feature_index.prompt is not None:
                    window.feature_index.prompt.done(QMessageBox.StandardButton.No)
                if time.monotonic()>deadline: raise AssertionError('GUI search timed out')
                if stage==0 and len(window.model.items)==5:
                    window.image_captured(query)
                    stage=1
                elif stage==1 and window.image_search_dialog and window.image_search_dialog.worker is None:
                    dialog=window.image_search_dialog
                    assert dialog.hits,dialog.status.text()
                    assert dialog.hits[0]['name']=='sample2.tif'
                    assert dialog.hits[0]['page_number']==2
                    assert dialog.results.count()>=5
                    dialog.copy_path()
                    assert QApplication.clipboard().text()==str(root/'sample2.tif')
                    if os.environ.get('MEDIA_CATALOG_QA_SCREENSHOT'):
                        dialog.grab().save(os.environ['MEDIA_CATALOG_QA_SCREENSHOT'])
                    dialog.select_in_catalog()
                    stage=2
                elif stage==2 and window.pending_search_selection is None and window.image_search_dialog is None:
                    index=window.view.currentIndex()
                    assert index.isValid()
                    assert window.model.items[index.row()]['name']=='sample2.tif'
                    window.image_captured(query)
                    assert window.image_search_dialog.worker is not None
                    # Quit with an active worker: cancellation must be awaited.
                    stage=3
                    timer.stop()
                    window.quit_app()
            except Exception as exc:
                print('GUI check failed:',repr(exc),flush=True)
                errors.append(repr(exc)); timer.stop(); window.quit_app()
        timer=QTimer(); timer.timeout.connect(tick); timer.start(50)
        QTimer.singleShot(40000,lambda:os._exit(2))
        app.exec()
        assert not errors,errors
        assert stage==3
        assert not window.service.isRunning()
        assert not window.feature_index.worker.isRunning()
        print('Image search UI, matching page, navigation, offline originals, active-worker shutdown: PASS')


if __name__=='__main__': main()
