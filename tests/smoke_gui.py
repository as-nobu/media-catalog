"""Run separately: python tests/smoke_gui.py (headless Qt, no user files)."""
import io
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from catalog import Catalog
from app import Window


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)/'source'
        root.mkdir()
        Image.new('RGB',(640,480),'steelblue').save(root/'sample.jpg')
        expected = 1
        if shutil.which('ffmpeg'):
            subprocess.run(['ffmpeg','-loglevel','error','-y','-f','lavfi','-i',
                'color=c=red:s=320x240:d=0.2',str(root/'sample.mp4')],check=True,timeout=15)
            expected += 1
        db = Catalog(Path(temp)/'state'/'catalog.db')
        db.add_root(root)
        window = Window(db)
        window.show()
        deadline = time.monotonic()+25
        errors = []
        def check():
            rows = db.rows()
            ready = len(rows)==expected and all(db.thumbnail(r['id']) for r in rows)
            if ready:
                window.refresh()
                for r in rows:
                    im = Image.open(io.BytesIO(db.thumbnail(r['id'])))
                    assert max(im.size)<=512
                # Wait until asynchronous DB loading reaches the displayed model.
                if not all(window.model.key(r) in window.model.cache for r in rows):
                    return
                for r in rows:
                    icon = window.model.cache[window.model.key(r)]
                    assert icon.cacheKey()!=window.model.placeholder.cacheKey()
                print(f'GUI, {expected} thumbnail types, asynchronous display: PASS',flush=True)
                timer.stop()
                window.quit_app()
            elif time.monotonic()>deadline:
                errors.append(repr(rows))
                timer.stop()
                window.quit_app()
        timer = QTimer()
        timer.timeout.connect(check)
        timer.start(250)
        # A hard deadline also catches quit/closeEvent regressions.
        QTimer.singleShot(35000,lambda:os._exit(2))
        app.exec()
        assert not errors, errors
        assert not window.service.isRunning()
        print('Clean worker and GUI shutdown: PASS',flush=True)


if __name__=='__main__':
    main()
