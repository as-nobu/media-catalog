import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,time,tempfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtCore import QItemSelectionModel
from app import Window,Service
from catalog import Catalog
from thumbnail_worker import generate
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp); (p/'sources').mkdir()
    pages=[Image.new('RGB',(800,600),color) for color in ['red','green','blue','yellow','magenta']]
    pages[0].save(p/'sources'/'a.tif',save_all=True,append_images=pages[1:])
    pages[0].save(p/'sources'/'b.png')
    db=Catalog(p/'data'/'catalog.sqlite3'); db.set_setting('generate',0)
    db.add_root(p/'sources'); db.scan(db.roots()[0])
    row=db.rows()[0]; out=p/'thumb.jpg'
    generate(row['path'],'image',out)
    db.save_thumb(row,out.read_bytes(),[(Path(str(out)+'.pages')/f'{i}.jpg').read_bytes() for i in range(1,6)],5)
    with patch.object(Service,'start'):
        w=Window(db)
    w.resize(1400,850); w.show(); app.processEvents()
    with patch('builtins.open',side_effect=AssertionError('source read')),patch('os.stat',side_effect=AssertionError('source stat')):
        w.view.setCurrentIndex(w.model.index(0))
        w.details.memo.setPlainText('保持するメモ')
        w.details.tags.setText('TIFF, 実験')
        w.details.favorite.setChecked(True)
        w.view.setCurrentIndex(w.model.index(1))
        assert db.rows()[0]['memo']=='保持するメモ'
        assert db.rows()[0]['favorite']==1
        w.favorites.setChecked(True); w.refresh()
        assert w.model.rowCount()==1
        w.pause_button.setChecked(True); assert w.service.paused
        w.pause_button.setChecked(False); assert not w.service.paused
        key=w.model.key(w.model.items[0]); request=(key,1100,700)
        w.view.hover_key=key
        captured=[]
        w.view.tiles_signals.ready.disconnect(w.view.tiles_ready)
        w.view.tiles_signals.ready.connect(lambda r,image: captured.append(image))
        w.view.build_tiles(request,w.model.items[0])
        assert captured and captured[0].width()<=1100 and captured[0].height()<=700
        # All five source colors must be present in the rendered contact sheet.
        img=captured[0]
        colors=[img.pixelColor(x,y).getRgb()[:3] for x in range(0,img.width(),10) for y in range(0,img.height(),10)]
        for expected in [(255,0,0),(0,128,0),(0,0,255),(255,255,0),(255,0,255)]:
            assert any(max(abs(a-b) for a,b in zip(actual,expected))<5 for actual in colors)
        assert not any(b.text()=='メタ情報・メモ' for b in w.findChildren(QPushButton))
    w.stats_timer.stop(); w.refresh_timer.stop(); w.details.timer.stop(); w.view.clear_hover()
    w.model.pool.shutdown(wait=True)
    w.exiting=True; w.close()
print('panel, annotations, favorites, pause, all-page hover DB-only: PASS')
