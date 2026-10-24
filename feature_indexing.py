"""Consent-based startup indexing and automatic indexing of newly saved previews.

The queue contains registration IDs, never source paths. Declining a startup
backlog does not prevent newly generated thumbnails from being indexed.
"""
from __future__ import annotations

import importlib.util
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox

from i18n import tr, translate_message
from visual_search import (SearchIndex, StandardEncoder, DinoEncoder, SearchCancelled,
                           check_cancel, dino_model_key)
from download_search_model import check_ai_packages, model_ready, ensure_model

# Counting only; stored vectors always use the actual model fingerprint.
DINO_PENDING = 'pending-dino-model-download'


class FeatureWorker(QThread):
    backlog = Signal(object)
    status = Signal(str, object)
    availability = Signal(object)
    busy = Signal(bool)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.index = SearchIndex(db)
        self.condition = threading.Condition()
        self.pending = OrderedDict()
        self.cancel_event = threading.Event()
        self.stopping = False
        self.model_signature = None
        self.model_key = None
        self.encoders = {}
        self.download_failures = {}
        self.job_active = False
        self.job_serial = 0
        self.job_uids = set()
        self.job_done = set()
        self.active_uids = set()

    def start_job(self):
        # Called with condition held. A job spans all internal 32-file batches.
        self.job_active = True
        self.job_serial += 1
        self.job_uids.clear()
        self.job_done.clear()

    def enqueue(self, uids, cached_uids=()):
        with self.condition:
            if not self.stopping:
                if not self.job_active:
                    self.start_job()
                self.job_uids.update(cached_uids)
                self.job_done.update(set(cached_uids) - self.pending.keys() - self.active_uids)
                for uid in uids:
                    self.pending[uid] = None
                    self.job_uids.add(uid)
                    self.job_done.discard(uid)
                self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.pending.clear()
            self.cancel_event.set()
            self.job_active = False

    def shutdown(self):
        with self.condition:
            self.stopping = True
            self.pending.clear()
            self.cancel_event.set()
            self.condition.notify_all()

    def say(self, text, **values):
        self.status.emit(text, values)

    def available(self):
        """Probe local dependencies and model identity, without model loading."""
        specs, unavailable = {}, {}
        if importlib.util.find_spec('cv2') is None:
            reason = 'OpenCVを読み込めません。IMAGE_SEARCH.ja.mdの導入手順を確認してください。'
            return specs, {'標準': reason, 'DINO': reason}
        specs['標準'] = (StandardEncoder.key, None)
        folder = Path(self.db.get_setting('image_search_model',
                      str(Path(__file__).with_name('models')/'dinov2-small'))).expanduser()
        try:
            check_ai_packages()
            if not model_ready(folder):
                if str(folder) in self.download_failures:
                    raise RuntimeError(self.download_failures[str(folder)])
                if importlib.util.find_spec('huggingface_hub') is None:
                    raise RuntimeError('モデル取得にはhuggingface_hubが必要です。IMAGE_SEARCH.ja.mdを確認してください。')
                specs['DINO'] = (DINO_PENDING, str(folder))
                return specs, unavailable
            self.download_failures.pop(str(folder), None)
            # Re-hash only when the selected folder or its local files change.
            stats = [(folder/name).stat() for name in ('config.json', 'preprocessor_config.json', 'model.safetensors')]
            signature = (str(folder), tuple((s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in stats))
            if signature != self.model_signature:
                self.model_key = dino_model_key(str(folder), self.cancel_event.is_set)
                self.model_signature = signature
            specs['DINO'] = (self.model_key, str(folder))
        except SearchCancelled:
            raise
        except FileNotFoundError:
            unavailable['DINO'] = 'DINOv2モデルがありません。IMAGE_SEARCH.ja.mdのAI設定手順を確認してください。'
        except Exception as exc:
            unavailable['DINO'] = str(exc)
        return specs, unavailable

    def inspect_startup(self):
        specs, unavailable = self.available()
        counts, uids = {}, set()
        for name, (key, _) in specs.items():
            check_cancel(self.cancel_event.is_set)
            pending = self.index.pending(key)
            counts[name] = sum(pending.values())
            uids.update(pending)
        return dict(counts=counts, uids=sorted(uids), unavailable=unavailable,
                    cached_uids=sorted(self.index.thumbnail_uids() - uids),
                    download=specs.get('DINO', (None,))[0] == DINO_PENDING and counts.get('DINO', 0) > 0)

    def download_progress(self, *args):
        self.say('DINOv2モデルを取得中…（初回のみ、約88 MB）')

    def process_batch(self, uids):
        with self.condition:
            if not self.job_active:
                self.start_job()
            self.job_uids.update(uids)
            serial = self.job_serial
        specs, unavailable = self.available()
        self.availability.emit(unavailable)
        completed = 0
        finished = {name: set() for name in specs}

        def report(name, uid=None):
            if uid is not None:
                finished[name].add(uid)
            with self.condition:
                if serial != self.job_serial:
                    return  # A cancelled job must not change a newly queued job.
                if uid is not None and uid not in self.pending and all(uid in group for group in finished.values()):
                    self.job_done.add(uid)
                done, total = len(self.job_done), len(self.job_uids)
            self.say('特徴抽出中: {engine} / 全体 {done} / {total}ファイル',
                     engine=name, done=done, total=total)

        for name, (key, folder) in specs.items():
            try:
                check_cancel(self.cancel_event.is_set)
                report(name)
                if not self.index.pending(key, uids=uids):
                    for uid in uids:
                        report(name, uid)
                    continue
                cached = self.encoders.get(name)
                if cached is None or cached.key != key:
                    # Discard the previous DINO model before loading a replacement.
                    self.encoders.pop(name, None)
                    if folder:
                        try:
                            ensure_model(folder, self.cancel_event.is_set, self.download_progress)
                        except SearchCancelled:
                            self.download_failures[folder] = 'モデル取得を中止しました。次回起動またはAI検索で再試行します。'
                            raise
                        except Exception as exc:
                            self.download_failures[folder] = str(exc)+'\n'+'モデル取得は次回起動またはAI検索で再試行します。'
                            raise RuntimeError(self.download_failures[folder]) from exc
                        self.say('DINOv2モデルを読み込み中…')
                        encoder = DinoEncoder(folder, self.cancel_event.is_set)
                    else:
                        encoder = StandardEncoder()
                    self.encoders[name] = encoder
                else:
                    encoder = cached
                # One bounded build/transaction buffer across files, rather than
                # opening and committing a DB connection for every single page.
                completed += self.index.build(encoder, self.cancel_event.is_set,
                    progress=lambda *args: report(name), uids=uids,
                    file_progress=lambda uid: report(name, uid))
            except SearchCancelled:
                raise
            except Exception as exc:
                # A broken optional engine must not prevent the other engine.
                unavailable[name] = str(exc)
        self.availability.emit(unavailable)
        return completed

    def run(self):
        try:
            self.say('未抽出のサムネイルを確認中…')
            report = self.inspect_startup()
            self.availability.emit(report['unavailable'])
            self.backlog.emit(report)
            self.say('特徴抽出は待機中です。')
        except SearchCancelled:
            pass
        except Exception as exc:
            self.say('特徴抽出の確認に失敗しました: {error}', error=str(exc))
        try:
            while True:
                with self.condition:
                    while not self.pending and not self.stopping:
                        self.condition.wait()
                    if self.stopping:
                        return
                    # Bound each batch so generation events and cancellation stay responsive.
                    uids = [self.pending.popitem(last=False)[0] for _ in range(min(32, len(self.pending)))]
                    self.active_uids = set(uids)
                    self.cancel_event.clear()
                self.busy.emit(True)
                try:
                    self.process_batch(uids)
                except SearchCancelled:
                    self.say('特徴抽出を中止しました。完了済みデータは保持します。')
                except Exception as exc:
                    self.say('特徴抽出を保留しました: {error}', error=str(exc))
                finally:
                    with self.condition:
                        self.active_uids.clear()
                        if not self.pending:
                            self.job_active = False
                            self.busy.emit(False)
                            if not self.cancel_event.is_set():
                                done, total = len(self.job_done), len(self.job_uids)
                                if done == total:
                                    self.say('特徴抽出完了: 全体 {done} / {total}ファイル', done=done, total=total)
                                else:
                                    self.say('特徴抽出を一部保留: 全体 {done} / {total}ファイル', done=done, total=total)
        finally:
            # Release the optional model on its owning worker thread.
            self.encoders.clear()


class FeatureIndexPanel(QWidget):
    """Non-blocking progress and a single, explicit startup confirmation."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.closing = False
        self.prompt = None
        self.state = ('未抽出のサムネイルを確認中…', {})
        self.unavailable = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row = QHBoxLayout()
        self.label = QLabel()
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.cancel_button = QPushButton(tr('特徴抽出を中止'))
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.worker = FeatureWorker(db, self)
        self.worker.status.connect(self.set_status)
        self.worker.availability.connect(self.set_availability)
        self.worker.busy.connect(self.cancel_button.setEnabled)
        self.worker.backlog.connect(self.confirm_startup)
        self.cancel_button.clicked.connect(self.worker.cancel)
        self.retranslate()

    def start(self):
        if not self.closing:
            self.worker.start()

    def thumbnail_saved(self, uid):
        self.worker.enqueue([uid])

    def set_status(self, text, values):
        self.state = (text, values)
        self.retranslate()

    def set_availability(self, reasons):
        self.unavailable = reasons
        self.retranslate()

    def retranslate(self):
        text, values = self.state
        self.label.setText(tr(text, **{k: translate_message(v) if isinstance(v, str) else v
                                      for k, v in values.items()}))
        self.notice.setText('\n'.join(tr('{engine}の特徴抽出は保留: {reason}',
                            engine=tr(name), reason=translate_message(reason))
                            for name, reason in self.unavailable.items()))
        self.notice.setVisible(bool(self.unavailable))
        self.cancel_button.setText(tr('特徴抽出を中止'))

    def confirm_startup(self, report):
        if self.closing or not report['uids']:
            return
        prompt = QMessageBox(self.window())
        prompt.setWindowTitle(tr('未抽出の特徴データ'))
        counts = '\n'.join(tr('{engine}: {count}ページ', engine=tr(name), count=count)
                           for name, count in report['counts'].items())
        prompt.setText(tr('保存済みサムネイルに未抽出の特徴があります。\n{counts}\n\n今からバックグラウンドで特徴抽出しますか？', counts=counts))
        note = tr('元ファイルは読み込みません。「いいえ」で既存分を保留しても、今後生成されるサムネイルは自動で特徴抽出します。')
        if report.get('download'):
            note += '\n'+tr('DINOv2モデルが未取得のため、初回に約88 MBをダウンロードします。画像は送信しません。')
        if report['unavailable']:
            note += '\n' + '\n'.join(tr('{engine}の特徴抽出は保留: {reason}',
                engine=tr(name), reason=translate_message(reason)) for name, reason in report['unavailable'].items())
        prompt.setInformativeText(note)
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        prompt.setDefaultButton(QMessageBox.StandardButton.No)
        self.prompt = prompt

        def answered(result):
            if not self.closing and result == QMessageBox.StandardButton.Yes:
                self.worker.enqueue(report['uids'], cached_uids=report.get('cached_uids', ()))
            self.prompt = None
            prompt.deleteLater()

        prompt.finished.connect(answered)
        prompt.open()

    def shutdown(self):
        self.closing = True
        if self.prompt is not None:
            self.prompt.reject()
        self.worker.shutdown()
