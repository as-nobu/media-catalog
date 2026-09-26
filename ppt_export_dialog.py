"""Responsive export progress and process lifetime, including cloud download waits."""
import json
from pathlib import Path
import shutil
import sys
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout
from i18n import tr


class PptExportDialog(QDialog):
    def __init__(self, paths, timeout, max_image_mp, parent=None, worker_script=None):
        super().__init__(parent)
        self.setWindowTitle(tr('選択画像をPowerPointへ出力'))
        self.resize(560,180)
        layout = QVBoxLayout(self)
        self.label = QLabel(tr('元画像を取得しています。未キャッシュのファイルはダウンロードします。'))
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True); layout.addWidget(self.label)
        bar = QProgressBar(); bar.setRange(0,0); layout.addWidget(bar)
        self.cancel_button = QPushButton(tr('キャンセル'))
        self.cancel_button.clicked.connect(self.reject); layout.addWidget(self.cancel_button)
        self.directory = Path(tempfile.mkdtemp(prefix='MediaCatalog-PPT-'))
        self.output = self.directory/'images.pptx'
        self.error = ''
        self.result_info = None
        self.cancelled = False
        self._finished = False
        self.buffer = b''
        self.stderr = b''
        request = self.directory/'request.json'
        request.write_text(json.dumps(dict(paths=[str(p) for p in paths],output=str(self.output),
                                          max_image_mp=max_image_mp)),encoding='utf-8')
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONIOENCODING','utf-8')
        self.process.setProcessEnvironment(environment)
        self.process.setProgram(sys.executable)
        self.process.setArguments([str(worker_script or Path(__file__).with_name('ppt_export.py')),str(request)])
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.process_error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(max(1,int(timeout*1000)))
        self.timer.timeout.connect(self.timed_out)
        QTimer.singleShot(0,self.start)

    def start(self):
        if self._finished or self.cancelled:
            return
        self.timer.start()  # includes startup, cloud download, decode and saving
        self.process.start()

    def read_progress(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line,self.buffer = self.buffer.split(b'\n',1)
            try:
                info = json.loads(line)
            except (ValueError,UnicodeError):
                continue
            stage = info.get('stage')
            if stage == 'source':
                self.label.setText(tr('取得中 {v0}/{v1}: {v2}',v0=info['file'],v1=info['files'],v2=info['name']))
            elif stage == 'page':
                self.label.setText(tr('画像を準備中: {v0} ({v1}/{v2}ページ)',v0=info['name'],v1=info['page'],v2=info['pages']))
            elif stage == 'saving':
                self.label.setText(tr('PPTを保存中…'))
            elif stage == 'done':
                self.result_info = info

    def read_error(self):
        self.stderr = (self.stderr+bytes(self.process.readAllStandardError()))[-4000:]

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.error = self.process.errorString()
            self.complete(-1,QProcess.ExitStatus.CrashExit)

    def timed_out(self):
        self.error = tr('PPT出力がタイムアウトしました。取得・生成タイムアウトの設定を確認してください。')
        self.reject()

    def reject(self):
        if self._finished:
            super().reject()
            return
        self.cancelled = True
        self.cancel_button.setEnabled(False)
        self.label.setText(tr('中止しています…'))
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.complete(-1,QProcess.ExitStatus.CrashExit)
        else:
            self.process.kill()

    def complete(self, code, status):
        if self._finished:
            return
        self.read_progress(); self.read_error()
        self._finished = True
        self.timer.stop()
        if not self.cancelled and code == 0 and self.result_info and self.output.is_file():
            # Keep successful PPTX until the user saves it elsewhere. Never delete on quit.
            (self.directory/'request.json').unlink(missing_ok=True)
            super().accept()
        else:
            if not self.error and not self.cancelled:
                self.error = self.stderr.decode('utf-8',errors='replace') or tr('PPT出力に失敗しました。')
            # The process is gone, so no decoder holds these app-owned files open.
            shutil.rmtree(self.directory,ignore_errors=True)
            super().reject()

    def closeEvent(self, event):
        if self._finished:
            event.accept()
        else:
            event.ignore()
            self.reject()
