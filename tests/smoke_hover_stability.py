import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,time,io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import *
from PIL import Image
from PySide6.QtTest import QTest
app=QApplication([])
def pump(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents();time.sleep(.01)
with tempfile.TemporaryDirectory() as t:
    p=Path(t);(p/'source').mkdir();(p/'source'/'a.tif').write_bytes(b'fixture')
    db=Catalog(p/'state'/'db.sqlite3');db.add_root(p/'source');db.scan(db.roots()[0]);row=db.rows()[0]
    b=io.BytesIO();Image.new('RGB',(512,384),'red').save(b,'JPEG');blob=b.getvalue()
    model=ThumbnailModel(db);view=HoverListView();view.setModel(model)
    view.setIconSize(QSize(180,140));view.setViewMode(QListView.ViewMode.IconMode);view.setGridSize(QSize(210,188));view.resize(700,600);view.show()
    for count in (8,16):
        db.save_thumb(row,blob,pages=[blob]*count,page_count=count);record=db.rows()[0]
        model.reset([record]);pump(.1)
        for offset in (QPoint(0,0),QPoint(40,20)):
            pos=view.visualRect(model.index(0)).center()+offset
            QCursor.setPos(view.viewport().mapToGlobal(pos));QTest.mouseMove(view.viewport(),pos)
            view.hover_key=model.key(record);view.show_hover();pump(.8)
            assert view.popup.isVisible()
            QApplication.sendEvent(view.viewport(),QEvent(QEvent.Type.Leave));pump(.3)
            assert view.popup.isVisible(),'Synthetic Leave must not dismiss popup'
            assert view.popup.windowFlags() & Qt.WindowType.WindowTransparentForInput
            QCursor.setPos(view.viewport().mapToGlobal(QPoint(-30,-30)));pump(.3)
            assert not view.popup.isVisible(),'Real leave must dismiss popup'
    view.close();model.pool.shutdown(wait=True)
print('8/16 pages, synthetic Leave stability, real leave: PASS')
