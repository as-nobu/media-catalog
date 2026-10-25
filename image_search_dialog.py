"""Cancellable visual search UI; displays only cached page thumbnails."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QBuffer, QIODevice
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QProgressBar)

from i18n import tr, translate_message


def image_bytes(image):
    image = image.scaled(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise RuntimeError(tr('検索画像を読み込めません。'))
    return bytes(buffer.data())


class SearchWorker(QThread):
    progress = Signal(str, int, int)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, db, query, model_dir, allowed, detailed, rebuild, parent=None):
        super().__init__(parent)
        self.db, self.query = db, query
        self.model_dir = model_dir
        self.allowed, self.detailed, self.rebuild = allowed, detailed, rebuild
        self.stop_event = threading.Event()
        self.last_progress = 0.

    def report(self, stage, done, total):
        now = time.monotonic()
        if now-self.last_progress >= .1 or done == total:
            self.last_progress = now
            self.progress.emit(stage, done, total)

    def run(self):
        from visual_search import SearchIndex, DinoEncoder, SearchCancelled
        from download_search_model import check_ai_packages, model_ready, ensure_model
        try:
            self.progress.emit('model', 0, 0)
            check_ai_packages()
            if not model_ready(self.model_dir) and not SearchIndex(self.db).pending('pending-dino-model-download'):
                raise RuntimeError('保存済みサムネイルがありません。先にサムネイルを生成してください。')
            ensure_model(self.model_dir, self.stop_event.is_set, self.report)
            self.progress.emit('model', 0, 0)
            encoder = DinoEncoder(self.model_dir, self.stop_event.is_set)
            report = SearchIndex(self.db).search(self.query, encoder, self.stop_event.is_set,
                self.report, self.allowed, self.detailed, rebuild=self.rebuild)
            if self.stop_event.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(report)
        except SearchCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ImageSearchPanel(QWidget):
    """Search controls only; results use the main catalog model and details pane."""
    capture_requested = Signal()
    results_ready = Signal(object)
    toggle_results_requested = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.worker = None
        self.closing = False
        self.query = b''
        self.allowed_uids = None
        self.scope_provider = lambda: self.allowed_uids
        self.showing_results = False
        self.status_state = ('', {})
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.query_preview = QLabel()
        self.query_preview.setFixedSize(64, 48)
        self.query_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.query_preview)
        content = QVBoxLayout()
        content.setSpacing(2)
        layout.addLayout(content, 1)
        actions = QHBoxLayout()
        self.capture_button = QPushButton(tr('範囲選択'))
        self.capture_button.clicked.connect(self.capture_requested)
        actions.addWidget(self.capture_button)
        self.search_button = QPushButton(tr('検索'))
        self.search_button.clicked.connect(self.start_search)
        self.search_button.setEnabled(False)
        actions.addWidget(self.search_button)
        self.cancel_button = QPushButton(tr('中止'))
        self.cancel_button.clicked.connect(self.cancel_search)
        self.cancel_button.setEnabled(False)
        actions.addWidget(self.cancel_button)
        self.scope = QCheckBox(tr('現在のカタログの絞り込み内'))
        actions.addWidget(self.scope)
        self.detailed = QCheckBox(tr('全件の部分一致を照合（低速）'))
        # Use only the explicit opt-in, never the old automatically saved default.
        self.detailed.setChecked(db.get_setting('image_search_exhaustive_v2','0')=='1')
        self.detailed.setToolTip(tr('通常は全画像の類似度を比較し、上位300ページの特徴点を照合します。ONでは全ページを照合します。'))
        actions.addWidget(self.detailed)
        actions.addStretch(1)
        self.toggle_button = QPushButton(tr('検索結果を表示'))
        self.toggle_button.setEnabled(False)
        self.toggle_button.clicked.connect(self.toggle_results_requested)
        actions.addWidget(self.toggle_button)
        content.addLayout(actions)
        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        status_row.addWidget(self.status,1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setRange(0,1)
        self.progress.setValue(0)
        self.progress.hide()
        status_row.addWidget(self.progress)
        content.addLayout(status_row)
        self.say('「範囲選択」で検索する画像を指定してください。')

    def set_query(self, image):
        self.query = image_bytes(image)
        self.query_preview.setPixmap(QPixmap.fromImage(image).scaled(self.query_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.start_search()

    def say(self, text, **values):
        self.status_state = (text, values)
        self.status.setText(tr(text, **values))

    def set_results_visible(self, visible):
        self.showing_results = visible
        self.toggle_button.setEnabled(True)
        self.retranslate()

    def retranslate(self):
        self.toggle_button.setText(tr('通常の一覧に戻る') if self.showing_results else tr('検索結果を表示'))
        text, values = self.status_state
        self.status.setText(tr(text, **values))

    def start_search(self):
        if self.worker is not None or self.closing or not self.query: return
        # Keep previously configured model locations without exposing an editor.
        folder = (self.db.get_setting('image_search_model','').strip() or
                  str(Path(__file__).with_name('models')/'dinov2-small'))
        for key, value in (('image_search_engine','dino'), ('image_search_model',folder),
                           ('image_search_exhaustive_v2',str(int(self.detailed.isChecked())))):
            if self.db.get_setting(key,'') != value: self.db.set_setting(key,value)
        self.say('検索を準備しています…')
        self.progress.setRange(0,0)
        self.set_busy(True)
        worker = SearchWorker(self.db,self.query,folder,
            self.scope_provider() if self.scope.isChecked() else None,
            self.detailed.isChecked(),False,self)
        self.worker = worker
        worker.progress.connect(self.show_progress)
        worker.completed.connect(self.show_results)
        worker.failed.connect(lambda message:self.say('画像検索に失敗しました: {error}',error=translate_message(message)))
        worker.cancelled.connect(lambda:self.say('検索を中止しました。前の検索結果は保持されています。'))
        worker.finished.connect(self.worker_finished)
        worker.start()

    def set_busy(self, busy):
        for widget in (self.capture_button,self.scope,self.detailed):
            widget.setEnabled(not busy)
        self.search_button.setEnabled(not busy and bool(self.query))
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)

    def show_progress(self, stage, done, total):
        labels = {'model':'検索モデルを準備中…','download':'DINOv2モデルを取得中…（初回のみ、約88 MB）',
                  'index':'検索データを準備中: {v0}/{v1}ページ',
                  'rank':'類似度を比較中: {v0}ページ','match':'部分一致を照合中: {v0}/{v1}ページ'}
        self.say(labels[stage],v0=done,v1=total)
        self.progress.setRange(0,total if total else 0)
        self.progress.setValue(done)

    def show_results(self, report):
        if self.closing: return
        self.say('画像検索: {pages}ページを比較 / 特徴点照合 {compared}ページ / 結果 {hits}ファイル / 未生成 {absent} / 読込不可 {errors}',
                 pages=report.pages, compared=report.compared, hits=len(report.hits),
                 absent=report.without_thumbnail, errors=report.errors)
        self.results_ready.emit(report)

    def cancel_search(self):
        if self.worker:
            self.worker.stop_event.set()
            self.cancel_button.setEnabled(False)
            self.say('中止しています…')

    def worker_finished(self):
        worker, self.worker = self.worker, None
        if worker: worker.deleteLater()
        self.set_busy(False)
        self.progress.setRange(0,1)
        self.progress.setValue(1)

    def shutdown(self):
        self.closing = True
        self.cancel_search()
