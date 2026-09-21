import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,time,sqlite3
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import *
from PySide6.QtGui import QCloseEvent
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp);(p/'sources').mkdir();(p/'sources'/'a.jpg').write_bytes(b'x')
    db=Catalog(p/'data'/'db.sqlite3');db.add_root(p/'sources');db.scan(db.roots()[0])
    with patch.object(Service,'start'): w=Window(db)
    w.show()
    deadline=time.monotonic()+5
    while not w.model.items and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert w.model.items
    w.view.setCurrentIndex(w.model.index(0))
    assert not w.windowIcon().isNull() and not w.tray.icon().isNull()
    assert all('編集を破棄' not in b.text() for b in w.findChildren(QPushButton))
    w.details.memo.setPlainText('トレイ収納')
    with patch.object(w.tray,'isVisible',return_value=True),patch.object(w.tray,'showMessage'):
        w.closeEvent(QCloseEvent())
    assert db.rows()[0]['memo']=='トレイ収納'
    w.show();w.details.memo.setPlainText('バックアップ前')
    destination=p/'backup.sqlite3'
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(destination),'')),patch.object(QMessageBox,'information'):
        w.backup_db()
        deadline=time.monotonic()+5
        while w.backup_running and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert not w.backup_running
    with sqlite3.connect(destination) as c:
        assert c.execute('SELECT memo FROM files').fetchone()[0]=='バックアップ前'
    w.details.timer.stop();w.stats_timer.stop();w.refresh_timer.stop();w.model.pool.shutdown()
    w.exiting=True;w.db_poll.stop();w.ui_pool.shutdown(wait=True);w.close()
print('tray/backup save and application icons: PASS')
