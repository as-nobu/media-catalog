"""Cancellable visual search UI; displays only cached page thumbnails."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QThread, Signal, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPixmap, QIcon, QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QFileDialog, QCheckBox, QProgressBar,
    QListWidget, QListWidgetItem, QListView, QPlainTextEdit, QSplitter, QWidget)

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

    def __init__(self, db, query, mode, model_dir, allowed, detailed, rebuild, parent=None):
        super().__init__(parent)
        self.db, self.query = db, query
        self.mode, self.model_dir = mode, model_dir
        self.allowed, self.detailed, self.rebuild = allowed, detailed, rebuild
        self.stop_event = threading.Event()
        self.last_progress = 0.

    def report(self, stage, done, total):
        now = time.monotonic()
        if now-self.last_progress >= .1 or done == total:
            self.last_progress = now
            self.progress.emit(stage, done, total)

    def run(self):
        from visual_search import SearchIndex, StandardEncoder, DinoEncoder, SearchCancelled
        from download_search_model import check_ai_packages, model_ready, ensure_model
        try:
            self.progress.emit('model', 0, 0)
            if self.mode == 'dino':
                check_ai_packages()
                if not model_ready(self.model_dir) and not SearchIndex(self.db).pending('pending-dino-model-download'):
                    raise RuntimeError('保存済みサムネイルがありません。先にサムネイルを生成してください。')
                ensure_model(self.model_dir, self.stop_event.is_set, self.report)
                self.progress.emit('model', 0, 0)
            encoder = (DinoEncoder(self.model_dir, self.stop_event.is_set)
                       if self.mode == 'dino' else StandardEncoder())
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


class ImageSearchDialog(QDialog):
    capture_requested = Signal()
    catalog_requested = Signal(str)

    def __init__(self, db, image, allowed_uids=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(tr('画像検索 ? 保存済みサムネイル'))
        self.resize(1080, 760)
        self.db, self.allowed_uids = db, allowed_uids
        self.worker = None
        self.closing = False
        self.query = b''
        self.hits = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.query_preview = QLabel()
        self.query_preview.setFixedSize(140, 100)
        self.query_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.query_preview)
        note = QLabel(tr('保存済み画像だけを検索します。元ファイルの取得は行いません。\n未抽出の特徴は検索時にも補完します。中断後は続きから処理します。'))
        note.setWordWrap(True)
        header.addWidget(note, 1)
        self.capture_button = QPushButton(tr('範囲を選び直す'))
        self.capture_button.clicked.connect(self.capture_requested)
        header.addWidget(self.capture_button)
        layout.addLayout(header)
        settings = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem(tr('標準（色・構造・部分一致）'), 'standard')
        self.mode.addItem(tr('AI（DINOv2＋部分一致）'), 'dino')
        self.mode.setCurrentIndex(max(0,self.mode.findData(db.get_setting('image_search_engine','standard'))))
        settings.addWidget(self.mode)
        self.scope = QCheckBox(tr('現在のカタログの絞り込み内'))
        settings.addWidget(self.scope)
        self.detailed = QCheckBox(tr('部分一致を詳しく検索'))
        self.detailed.setChecked(db.get_setting('image_search_detailed','1')=='1')
        self.detailed.setToolTip(tr('全候補の特徴点を照合します。件数が多い場合は時間がかかります。OFFでは上位300ページを照合します。'))
        settings.addWidget(self.detailed)
        settings.addStretch()
        layout.addLayout(settings)
        self.ai_panel = QWidget()
        ai = QHBoxLayout(self.ai_panel)
        ai.setContentsMargins(0,0,0,0)
        ai.addWidget(QLabel(tr('モデルフォルダ')))
        default_model = str(Path(__file__).with_name('models')/'dinov2-small')
        self.model_dir = QLineEdit(db.get_setting('image_search_model', default_model))
        ai.addWidget(self.model_dir, 1)
        self.browse = QPushButton(tr('参照'))
        self.browse.clicked.connect(self.choose_model)
        ai.addWidget(self.browse)
        layout.addWidget(self.ai_panel)
        self.mode.currentIndexChanged.connect(lambda:self.ai_panel.setVisible(self.mode.currentData()=='dino'))
        self.ai_panel.setVisible(self.mode.currentData()=='dino')
        actions = QHBoxLayout()
        self.search_button = QPushButton(tr('検索'))
        self.search_button.setObjectName('primary')
        self.search_button.clicked.connect(self.start_search)
        self.cancel_button = QPushButton(tr('中止'))
        self.cancel_button.clicked.connect(self.cancel_search)
        self.cancel_button.setEnabled(False)
        self.rebuild = QCheckBox(tr('検索データを再作成'))
        self.rebuild.setToolTip(tr('検索用データだけを作り直します。元画像・サムネイルの再取得はしません。'))
        actions.addWidget(self.search_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.rebuild)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        actions.addWidget(self.progress, 1)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter()
        self.results = QListWidget()
        self.results.setViewMode(QListView.ViewMode.IconMode)
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMovement(QListView.Movement.Static)
        self.results.setIconSize(QSize(160, 120))
        self.results.setGridSize(QSize(195, 185))
        self.results.setWordWrap(True)
        self.results.currentRowChanged.connect(self.select_result)
        split.addWidget(self.results)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        self.preview = QLabel(tr('結果を選択してください。'))
        self.preview.setMinimumSize(260, 230)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(self.preview)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        side_layout.addWidget(self.info, 1)
        self.copy_button = QPushButton(tr('ファイルパスをコピー'))
        self.copy_button.clicked.connect(self.copy_path)
        side_layout.addWidget(self.copy_button)
        self.catalog_button = QPushButton(tr('カタログで選択'))
        self.catalog_button.clicked.connect(self.select_in_catalog)
        side_layout.addWidget(self.catalog_button)
        split.addWidget(side)
        split.setSizes([720, 330])
        layout.addWidget(split, 1)
        close_button = QPushButton(tr('閉じる'))
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
        self.set_query(image)

    def choose_model(self):
        folder = QFileDialog.getExistingDirectory(self, tr('モデルフォルダ'), self.model_dir.text())
        if folder:
            self.model_dir.setText(folder)

    def set_query(self, image):
        self.query = image_bytes(image)
        self.query_preview.setPixmap(QPixmap.fromImage(image).scaled(140,100,
            Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.start_search()

    def start_search(self):
        if self.worker is not None or self.closing:
            return
        mode, folder = self.mode.currentData(), self.model_dir.text().strip()
        self.db.set_setting('image_search_engine',mode)
        self.db.set_setting('image_search_model',folder)
        self.db.set_setting('image_search_detailed',int(self.detailed.isChecked()))
        self.results.clear()
        self.hits = []
        self.info.clear()
        self.preview.clear()
        self.copy_button.setEnabled(False)
        self.catalog_button.setEnabled(False)
        self.status.setText(tr('検索を準備しています…'))
        self.progress.setRange(0, 0)
        self.set_busy(True)
        worker = SearchWorker(self.db,self.query,mode,folder,
            self.allowed_uids if self.scope.isChecked() else None,
            self.detailed.isChecked(),self.rebuild.isChecked(),self)
        self.worker = worker
        worker.progress.connect(self.show_progress)
        worker.completed.connect(self.show_results)
        worker.failed.connect(self.show_error)
        worker.cancelled.connect(lambda:self.status.setText(tr('検索を中止しました。作成済みの検索データは保持されます。')))
        worker.finished.connect(self.worker_finished)
        worker.start()

    def set_busy(self, busy):
        for widget in (self.search_button,self.capture_button,self.mode,self.scope,self.detailed,
                       self.model_dir,self.browse,self.rebuild):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def show_progress(self, stage, done, total):
        labels = {'model':'検索モデルを準備中…','download':'DINOv2モデルを取得中…（初回のみ、約88 MB）',
                  'index':'検索データを準備中: {v0}/{v1}ページ',
                  'rank':'類似度を比較中: {v0}ページ','match':'部分一致を照合中: {v0}/{v1}ページ'}
        self.status.setText(tr(labels[stage], v0=done,v1=total))
        self.progress.setRange(0,total if total else 0)
        self.progress.setValue(done)

    def show_error(self, message):
        self.status.setText(tr('画像検索に失敗しました: ')+translate_message(message))

    def thumbnail(self, hit):
        blob = self.db.page_thumbnail(hit['id'],hit['registration_uid'],hit['revision'],hit['page_number'])
        if blob is None and hit['page_number']==1:
            blob = self.db.thumbnail(hit['id'],hit['registration_uid'],hit['revision'])
        image = QImage()
        if blob:
            image.loadFromData(blob)
        return image

    def show_results(self, report):
        if self.closing:
            return
        self.hits = report.hits
        for rank, hit in enumerate(self.hits, 1):
            image = self.thumbnail(hit)
            label = tr('{v0}. {v1}\n{v2}ページ',v0=rank,v1=hit['name'],v2=hit['page_number'])
            if hit['inliers']:
                label += '\n'+tr('部分一致の対応あり')
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)),label)
            item.setToolTip(hit['path'])
            self.results.addItem(item)
        self.status.setText(tr('検索対象 {v0}ページ / 表示 {v1}件 / サムネイルなし {v2}ファイル / 読込不可 {v3}ページ',
            v0=report.pages,v1=len(self.hits),v2=report.without_thumbnail,v3=report.errors))
        if self.hits:
            self.results.setCurrentRow(0)
        else:
            self.preview.setText(tr('検索できる保存済み画像がありません。'))

    def current_hit(self):
        i = self.results.currentRow()
        return self.hits[i] if 0 <= i < len(self.hits) else None

    def select_result(self, index):
        hit = self.current_hit()
        self.copy_button.setEnabled(hit is not None)
        self.catalog_button.setEnabled(hit is not None)
        if hit is None:
            return
        image = self.thumbnail(hit)
        if image.isNull():
            self.preview.setText(tr('画像が更新・削除されました。再検索してください。'))
        else:
            if hit['polygon']:
                # Engine coordinates refer to a preview of at most 512 px.
                scale = max(image.width(),image.height())/min(512,max(image.width(),image.height()))
                painter = QPainter(image)
                painter.setPen(QColor('#00e5ff'))
                painter.drawPolygon(QPolygonF([QPointF(x*scale,y*scale) for x,y in hit['polygon']]))
                painter.end()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(310,260,
                Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        text = hit['path']+'\n\n'+tr('一致ページ: {v0}',v0=hit['page_number'])
        text += '\n'+tr('類似度指標: {v0:.3f}（確率ではありません）',v0=hit['similarity'])
        if hit['inliers']:
            text += '\n'+tr('対応する特徴点: {v0}',v0=hit['inliers'])
        if hit['missing']:
            text += '\n'+tr('［削除候補］')
        if hit['thumb_size'] != hit['size'] or hit['thumb_mtime'] != hit['mtime_ns']:
            text += '\n'+tr('更新待ちの保存済み画像を検索しています。')
        text += '\n\n'+tr('表示・検索は保存済み画像のみです。元ファイルを開く場合はカタログから操作してください。')
        self.info.setPlainText(text)

    def copy_path(self):
        hit = self.current_hit()
        if hit:
            QApplication.clipboard().setText(hit['path'])

    def select_in_catalog(self):
        hit = self.current_hit()
        if hit:
            self.catalog_requested.emit(hit['registration_uid'])
            self.close()

    def cancel_search(self):
        if self.worker:
            self.worker.stop_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText(tr('中止しています…'))

    def worker_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.set_busy(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.rebuild.setChecked(False)
        if self.closing:
            self.close()

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.cancel_search()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.cancel_search()
            event.ignore()
        else:
            super().closeEvent(event)
