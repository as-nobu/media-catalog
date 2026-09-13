"""GUI regression: first-page icon, real hover, all pages, no source reads."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import tempfile
import time
import json
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import Qt,QSize,QEvent,QPoint
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import ThumbnailModel,HoverListView,PagesWindow,PageBadgeDelegate
from catalog import Catalog
from thumbnail_worker import generate

app=QApplication([])
app.setQuitOnLastWindowClosed(False)


def until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('GUI condition timed out')


with tempfile.TemporaryDirectory() as temp:
    base=Path(temp)
    root=base/'source'; root.mkdir()
    source=root/'five.tif'
    colors=['red','lime','blue','yellow','magenta']
    pages=[Image.new('RGB',(1024,768),c) for c in colors]
    pages[0].save(source,save_all=True,append_images=pages[1:])
    out=base/'thumb.jpg'
    generate(str(source),'image',str(out))
    count=json.loads(Path(str(out)+'.json').read_text())['page_count']
    db=Catalog(base/'state'/'db.sqlite')
    db.add_root(root); db.scan(db.roots()[0])
    row=db.rows()[0]
    db.save_thumb(row,out.read_bytes(),pages=((Path(str(out)+'.pages')/f'{i}.jpg').read_bytes() for i in range(1,count+1)),page_count=count)
    row=db.rows()[0]
    model=ThumbnailModel(db)
    view=HoverListView()
    view.setViewMode(view.ViewMode.IconMode)
    view.setIconSize(QSize(180,140)); view.setGridSize(QSize(210,188))
    view.setItemDelegate(PageBadgeDelegate(view))
    view.setModel(model); model.reset([row]); view.resize(640,480); view.show()
    dialog=PagesWindow(db,row)
    try:
        # Both GUI surfaces must work without stat / opening any source file.
        with patch('builtins.open',side_effect=AssertionError('unexpected file open')),patch('os.stat',side_effect=AssertionError('unexpected source metadata access')):
            model.data(model.index(0),Qt.ItemDataRole.DecorationRole)
            until(lambda:model.key(row) in model.cache)
            assert '5ページ' in model.data(model.index(0))
            icon=model.cache[model.key(row)]
            pix=icon.pixmap(QSize(512,512),1.0)
            assert pix.size()==QSize(512,384)
            assert pix.toImage().pixelColor(200,200).red()>240
            # Exercise actual mouse event, delay and DB-cache popup path.
            pos=view.visualRect(model.index(0)).center()
            QCursor.setPos(view.viewport().mapToGlobal(pos))
            QTest.mouseMove(view.viewport(),pos)
            until(lambda:view.popup.isVisible())
            tile=view.popup.pixmap().toImage()
            assert tile.width()>512
            samples=[tile.pixelColor(x,y) for x in range(0,tile.width(),10) for y in range(0,tile.height(),10)]
            assert any(c.red()>240 and c.blue()>240 and c.green()<10 for c in samples)
            QCursor.setPos(view.viewport().mapToGlobal(view.viewport().rect().bottomRight())+QPoint(30,30))
            QApplication.sendEvent(view.viewport(),QEvent(QEvent.Type.Leave))
            app.processEvents()
            assert not view.popup.isVisible()
            dialog.show()
            assert dialog.model.rowCount()==5
            for i in range(5):
                dialog.model.data(dialog.model.index(i),Qt.ItemDataRole.DecorationRole)
            until(lambda:len(dialog.model.cache)==5)
            fifth=dialog.model.cache[dialog.model.key(dialog.model.items[4])].pixmap(QSize(512,512),1.0)
            pixel=fifth.toImage().pixelColor(200,200)
            assert pixel.red()>240 and pixel.blue()>240 and pixel.green()<10
        view.grab().save(str(base/'main.png'))
        dialog.grab().save(str(base/'pages.png'))
        db.remove_folder(root)
        dialog.refresh_pages()
        assert dialog.model.rowCount()==0
        print('First-page icon, tiled hover, all five pages, DB-only reads, unregister: PASS')
    finally:
        dialog.close(); view.close()
        model.pool.shutdown(wait=True,cancel_futures=True)
        app.processEvents()
