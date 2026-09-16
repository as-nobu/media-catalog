import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication,QMessageBox
from catalog import Catalog
from import_dialog import ImportDialog
from i18n import set_language

app=QApplication([])
set_language('en')
with tempfile.TemporaryDirectory() as temp:
    base=Path(temp); root=base/'files'; root.mkdir(); (root/'cells.tif').write_bytes(b'fixture')
    db=Catalog(base/'state'/'db.sqlite3'); db.add_root(root); db.scan(db.roots()[0])
    row=db.rows()[0]
    db.save_annotations(row['registration_uid'],'Imported note','microscopy',False,('','',0))
    source=base/'backup.sqlite3'; db.backup(source)
    db.save_annotations(row['registration_uid'],'Local note','cells',False,('Imported note','microscopy',0))
    dialog=ImportDialog(db,str(source)); dialog.show(); app.processEvents()
    assert dialog.table.rowCount()==1
    dialog.table.cellWidget(0,2).setCurrentIndex(2)
    assert dialog.plan[0]['choice']=='both'
    assert dialog.current.toPlainText()=='Local note'
    assert dialog.incoming.toPlainText()=='Imported note'
    with patch.object(QMessageBox,'information',return_value=QMessageBox.StandardButton.Ok):
        dialog.apply()
        deadline=time.monotonic()+10
        while dialog.busy and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        assert not dialog.busy
    assert db.rows()[0]['memo']=='Local note\n\n---\nImported note'
    assert db.rows()[0]['tags']=='cells, microscopy'
    assert list((Path(db.path).parent/'backups').glob('*.sqlite3'))
print('Import GUI smoke passed')
