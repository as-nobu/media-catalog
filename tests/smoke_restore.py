import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys,tempfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from catalog import Catalog
from restore_dialog import RestoreDialog
from catalog_restore import selected_catalog
from app import Window
from i18n import set_language

app=QApplication([]);app.setQuitOnLastWindowClosed(False)
with tempfile.TemporaryDirectory() as temp:
    home=Path(temp)/'app';root=Path(temp)/'source';root.mkdir();(root/'a.jpg').write_bytes(b'fixture')
    db=Catalog(home/'catalog.sqlite3');db.add_root(root);db.scan(db.roots()[0]);db.set_setting('generate','0');db.set_setting('scan_enabled','0');db.set_setting('language','en')
    backup=Path(temp)/'backup.sqlite3';db.backup(backup)
    window=Window(db);window.data_home=home
    dialog=RestoreDialog(str(backup),home,window)
    dialog.paths[0].setText(str(Path(temp)/'new-location'))
    dialog.apply();deadline=time.monotonic()+10
    while dialog.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert dialog.result_path
    assert selected_catalog(home)==home/'catalog.sqlite3'
    window.pending_catalog=dialog.result_path
    window.quit_app();deadline=time.monotonic()+10
    while window.shutdown_timer.isActive() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert selected_catalog(home)==Path(dialog.result_path)
    restored=Catalog(selected_catalog(home))
    assert restored.rows()[0]['path']==str(Path(temp)/'new-location'/'a.jpg')
    assert restored.get_setting('scan_enabled','1')=='0'
print('Restore, shutdown and next-start selection passed')
