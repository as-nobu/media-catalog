import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,time
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication,QPushButton,QCheckBox
from app import Window,Service
from i18n import get_language,translate_message

app=QApplication([])
def wait_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert predicate(),'event loop deadline'

with tempfile.TemporaryDirectory() as temp:
    from catalog import Catalog
    p=Path(temp);(p/'source').mkdir();(p/'source'/'日本語の写真.jpg').write_bytes(b'fixture')
    db=Catalog(p/'state'/'db.sqlite3');db.add_root(p/'source');db.scan(db.roots()[0])
    with patch.object(Service,'start'):
        w=Window(db)
    w.show();wait_until(lambda:len(w.model.items)==1);w.view.setCurrentIndex(w.model.index(0))
    w.details.memo.setPlainText('日本語メモ {test}')
    w.details.tags.setText('実験, タグ')
    with patch('builtins.open',side_effect=AssertionError('source read')):
        for lang in (1,0,1):
            w.language_combo.setCurrentIndex(lang);app.processEvents()
            assert w.details.memo.toPlainText()=='日本語メモ {test}'
            assert w.details.tags.text()=='実験, タグ'
            assert w.details.dirty()
            assert '日本語の写真.jpg' in w.model.data(w.model.index(0))
            labels={b.text() for b in w.findChildren(QPushButton)}
            assert ('Add folder' if lang else 'フォルダ登録') in labels
            assert w.tree.headerItem().text(0)==('Registered folders' if lang else '登録フォルダ')
            assert ('Type:' if lang else '種類:') in w.details.info.toPlainText()
    assert db.get_setting('language','')=='en'
    assert 'timed out' in translate_message('取得・生成が1800秒でタイムアウトしました。再試行するまで保留します。')
    assert w.tray.contextMenu().actions()[-1].text()=='Quit'
    w.details.save()
    w.details.timer.stop();w.stats_timer.stop();w.refresh_timer.stop();w.db_poll.stop()
    w.ui_pool.shutdown(wait=True);w.model.pool.shutdown()
    w.exiting=True;w.close()
    with patch.object(Service,'start'):
        restored=Window(db)
    assert get_language()=='en' and restored.language_combo.currentData()=='en'
    restored.details.timer.stop();restored.stats_timer.stop();restored.refresh_timer.stop();restored.db_poll.stop()
    restored.ui_pool.shutdown(wait=True);restored.model.pool.shutdown()
    restored.exiting=True;restored.close()
print('language switch, unsaved edits, filenames, persistence: PASS')
