import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,time,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import *
from thumbnail_worker import generate
app=QApplication([])
with tempfile.TemporaryDirectory() as temp:
    p=Path(temp); (p/'source').mkdir(); source=p/'source'/'empty.pptx'; source.touch()
    db=Catalog(p/'state'/'db.sqlite3'); db.add_root(p/'source'); db.scan(db.roots()[0]); row=db.rows()[0]
    panel=DetailsWindow(db,row)
    panel.memo.setPlainText('自動保存メモ');panel.tags.setText('実験,');panel.favorite.setChecked(True)
    deadline=time.monotonic()+.7
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert db.rows()[0]['memo']==''
    assert panel.tags.text()=='実験,'
    panel.memo.setPlainText('終了直前')
    assert panel.close();assert db.rows()[0]['memo']=='終了直前'
    assert db.rows()[0]['tags']=='実験' and db.rows()[0]['favorite']==1
    with patch('subprocess.Popen',side_effect=AssertionError('Office launch')),patch('builtins.open',side_effect=AssertionError('source content read')):
        Service(db).make_thumbnail(row)
    result=db.rows()[0];assert result['page_count']==0 and result['error']==''
    assert result['thumb_size']==0 and db.next_job() is None
    generate(str(source),'powerpoint',p/'out.jpg')
    assert json.loads((p/'out.jpg.json').read_text())['page_count']==0
print('deferred save, close flush, zero-byte PPT no download/Office: PASS')
