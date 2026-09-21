import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,tempfile,time,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import QApplication,Window,Service,Catalog,QItemSelection,QItemSelectionModel,QTimer
app=QApplication([])
def wait_until(predicate,seconds=10):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert predicate(),'event loop deadline'
with tempfile.TemporaryDirectory() as temp:
    p=Path(temp);root=p/'sources';root.mkdir()
    db=Catalog(p/'data'/'db.sqlite3');db.add_root(root)
    root_id=db.roots()[0]['id']
    with db.connect() as c:
        c.executemany('INSERT INTO files(root_id,path,parent,name,kind,size,mtime_ns,registration_uid,error,metadata_json,changed_at,seen) VALUES(?,?,?,?,?,?,?,?,?,?,0,0)',
            [(root_id,str(root/f'{i:05}.tif'),str(root),f'{i:05}.tif','image',i+1,1,f'uid-{i}','failed','{"note":"'+('x'*1024)+'"}') for i in range(20000)])
    with patch.object(Service,'start'): w=Window(db)
    try:
        wait_until(lambda:len(w.model.items)==20000)
        selection=QItemSelection(w.model.index(0),w.model.index(19999))
        sm=w.view.selectionModel();sm.select(selection,QItemSelectionModel.SelectionFlag.ClearAndSelect)
        gate=threading.Event();started=threading.Event();real=db.regenerate
        def delayed(*args):
            started.set();assert gate.wait(10);return real(*args)
        ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(20)
        with patch.object(db,'regenerate',side_effect=delayed):
            begin=time.monotonic();w.regenerate();elapsed=time.monotonic()-begin
            assert elapsed<1,elapsed
            wait_until(lambda:started.is_set() and len(ticks)>=5)
            gate.set();wait_until(lambda:not w.regen_futures)
        wait_until(lambda:w.list_future is None and not w.refresh_timer.isActive())
        assert len(sm.selectedIndexes())==20000
        with patch.object(sm,'select',wraps=sm.select) as select:
            w.refresh();wait_until(lambda:w.list_future is None)
            assert select.call_count==0,select.call_count
        with db.connect() as c:
            assert c.execute("SELECT count(*) FROM files WHERE error='' AND thumb_size IS NULL").fetchone()[0]==20000
        w.sort_order.setCurrentIndex(3);w.refresh()
        wait_until(lambda:w.list_future is None and not w.refresh_timer.isActive())
        assert len(sm.selectedIndexes())==20000
        assert w.model.items[0]['name']=='19999.tif'
        load_gate=threading.Event();load_started=threading.Event();real_rows=db.rows
        def slow_rows(*args,**kwargs):
            load_started.set();assert load_gate.wait(10);return real_rows(*args,**kwargs)
        with patch.object(db,'rows',side_effect=slow_rows):
            w.refresh();wait_until(load_started.is_set)
            w.search.setText('00001')
            load_gate.set()
            wait_until(lambda:len(w.model.items)==1 and w.model.items[0]['name']=='00001.tif')
        print(f'20,000 selected: queue call {elapsed:.3f}s; responsive during DB wait; selection preserved')
    finally:
        gate.set();timer.stop();w.exiting=True
        w.db_poll.stop();w.refresh_timer.stop();w.stats_timer.stop();w.details.timer.stop();w.details.save_timer.stop()
        w.ui_pool.shutdown(wait=True);w.model.pool.shutdown(wait=True);w.tray.hide();w.close()
