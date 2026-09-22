import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import *
a=QApplication([])
def wait_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:
        a.processEvents();time.sleep(.01)
    assert predicate(),'event loop deadline'

with tempfile.TemporaryDirectory() as t:
    p=Path(t); (p/'source').mkdir()
    for name in ('ok.jpg','error.jpg','timeout.jpg'): (p/'source'/name).write_bytes(b'x')
    db=Catalog(p/'state'/'db.sqlite3'); db.add_root(p/'source'); db.scan(db.roots()[0])
    rows={r['name']:r for r in db.rows()}
    db.fail_job(rows['error.jpg'],'decode error')
    db.fail_job(rows['timeout.jpg'],'timeout',True)
    assert len(db.rows(error_only=True))==2
    assert len(db.rows(error_only=True,timeout_only=True))==1
    with patch.object(Service,'start'): w=Window(db)
    w.show();wait_until(lambda:w.model.rowCount()==3)
    w.errors.setChecked(True);w.refresh();wait_until(lambda:w.model.rowCount()==2)
    w.details_toggle.setChecked(False); assert w.details.isHidden()
    w.details_toggle.setChecked(True); assert not w.details.isHidden()
    w.splitter.setSizes([200,500,400]); w.save_panel_sizes()
    before=w.splitter.sizes(); w.details_toggle.setChecked(False); w.details_toggle.setChecked(True)
    assert abs(w.splitter.sizes()[2]-before[2])<=2
    area=a.primaryScreen().availableGeometry()
    for size in (QSize(int(area.width()*.85),int(area.height()*.6)),QSize(area.width()*2,area.height()*2)):
        pix=QPixmap(size); pix.fill(QColor('red'))
        w.view.display_popup(pix,area.bottomRight())
        a.processEvents()
        popup=w.view.popup
        assert area.contains(popup.geometry()),(area,popup.geometry())
        assert popup.width()>=popup.pixmap().width()+10
        assert popup.height()>=popup.pixmap().height()+10
    w.view.clear_hover();w.details.timer.stop();w.stats_timer.stop();w.refresh_timer.stop();w.db_poll.stop()
    w.ui_pool.shutdown(wait=True);w.model.pool.shutdown();w.exiting=True;w.close()
print('popup bounds, panel toggle/resize, error filtering: PASS')
