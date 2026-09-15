import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from catalog import Catalog
from app import Window

app=QApplication([])
with tempfile.TemporaryDirectory() as temp:
    db=Catalog(Path(temp)/'db.sqlite3')
    db.set_setting('generate','0')
    window=Window(db)
    try:
        window.show()
        app.processEvents()
        window.grab().save(str(Path(temp)/'preview.png'))
        window.language_combo.setCurrentIndex(1)
        app.processEvents()
        assert window.kind_filter.itemText(0)=='All formats'
        window.search.setText('tag:sample -bad')
        window.kind_filter.setCurrentIndex(1)
        window.favorites.setChecked(True)
        window.clear_filters()
        assert not window.search.text()
        assert window.kind_filter.currentData()==''
        assert not window.favorites.isChecked()
        window.language_combo.setCurrentIndex(0)
        assert window.sort_order.itemText(0)=='名前順'
    finally:
        window.service.stop()
        window.service.wait(5000)
        window.exiting=True
        window.close()
print('Modern UI smoke passed')
