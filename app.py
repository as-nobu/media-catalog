from __future__ import annotations
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import queue
import json
import math
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import (Qt, QAbstractListModel, QModelIndex, QSize, QThread,
                            Signal, QObject, QTimer, QUrl, QLockFile, QStandardPaths, QEvent, QPoint, QRect)
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices, QAction, QColor, QCursor, QImage, QPainter
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTreeWidget, QTreeWidgetItem, QListView, QSplitter, QCheckBox,
    QLineEdit, QSpinBox, QLabel, QFileDialog, QMessageBox, QSystemTrayIcon, QMenu,
    QAbstractItemView, QStyle, QComboBox)
from PySide6.QtWidgets import QTreeWidgetItemIterator, QDialog, QStyledItemDelegate, QPlainTextEdit
from datetime import datetime
from catalog import Catalog, norm, within
from limits import MAX_PAGE_THUMBNAILS, EMPTY_FILE_KINDS
from theme import STYLE
from i18n import set_language, get_language, tr, translate_message, static_source
from qt_i18n import install_dialog_translator


class Service(QThread):
    changed = Signal()
    message = Signal(str)
    copy_ready = Signal(str)
    progress = Signal(object)

    def __init__(self, db, worker_script=None):
        super().__init__()
        self.db = db
        self.worker_script = str(worker_script or Path(__file__).with_name('thumbnail_worker.py'))
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.scan_requested = threading.Event()
        self.scan_enabled = db.get_setting('scan_enabled','1')=='1'
        if self.scan_enabled:self.scan_requested.set()
        self.interval = int(db.get_setting('interval',600))
        self.generate = db.get_setting('generate','1') == '1'
        self.timeout = int(db.get_setting('timeout',1800))
        self.open_requests = queue.Queue()
        self.paused = db.get_setting('paused','0') == '1'

    def open_copy(self, row):
        self.open_requests.put(dict(row, open_copy=True))
        self.wake.set()

    def request_scan(self):
        self.scan_requested.set()
        self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def run(self):
        last_scan = 0
        active, scan_future = {}, None
        # Scanning never waits for source hydration / thumbnail decoding.
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='scan-control') as scanners, \
             ThreadPoolExecutor(max_workers=3,thread_name_prefix='thumbnail-control') as generators:
            while not self.stop_event.is_set():
                try:
                    if scan_future is not None and scan_future.done():
                        scan_future.result()
                        scan_future = None
                        last_scan = time.monotonic()
                        self.changed.emit()
                    if scan_future is None and (self.scan_requested.is_set() or (self.scan_enabled and time.monotonic()-last_scan >= self.interval)):
                        self.scan_requested.clear()
                        scan_future = scanners.submit(self.scan_all)
                    for future in list(active):
                        if future.done():
                            active.pop(future)
                            future.result()
                            self.changed.emit()
                    while len(active)<3 and not self.stop_event.is_set():
                        try:
                            job = self.open_requests.get_nowait()
                        except queue.Empty:
                            job = self.db.next_job(exclude=active.values(),
                                skip_office=any(getattr(f,'office_job',False) for f in active)) if self.generate and not self.paused else None
                        if not job:
                            break
                        # Serialize Office generation; image/video remain concurrent.
                        if job['kind']=='powerpoint' and not job.get('open_copy'):
                            if any(getattr(f,'office_job',False) for f in active):
                                break
                        future = generators.submit(self.make_thumbnail,job)
                        future.office_job = job['kind']=='powerpoint' and not job.get('open_copy')
                        active[future] = job['id']
                        future.label = job['name']
                        future.started = time.monotonic()
                except Exception as exc:
                    self.message.emit('バックグラウンド処理エラー: '+str(exc))
                    if scan_future is not None and scan_future.done():
                        scan_future = None
                        last_scan = time.monotonic()
                self.progress.emit([(f.label,int(time.monotonic()-f.started)) for f in active])
                self.wake.wait(.2)
                self.wake.clear()

    def scan_all(self):
        for root in self.db.roots():
            if self.stop_event.is_set():
                break
            self.message.emit('メタデータ確認中（4スレッド）: '+root['path'])
            count, errors = self.db.scan(root,self.stop_event.is_set,workers=4)
            self.message.emit(f'{count:,}件確認 / '+ ('確認不能あり（左のフォルダに詳細）' if errors else '完了'))

    def make_thumbnail(self, job):
        self.message.emit('取得・処理中: '+job['name'])
        timeout = self.timeout
        process = None
        try:
            st = os.stat(job['path'],follow_symlinks=False)
            if (st.st_size,st.st_mtime_ns) != (job['size'],job['mtime_ns']):
                self.request_scan()
                return
            if job['kind'] in EMPTY_FILE_KINDS and job['size']==0 and not job.get('open_copy'):
                self.db.save_thumb(job,None,pages=[],page_count=0,metadata={'状態':'空ファイル（0 bytes）'})
                return
            work = Path(self.db.path).parent/'work'
            work.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='job-',dir=work) as temp:
                out = Path(temp)/'thumbnail.jpg'
                if job.get('open_copy'):
                    preview_root = Path(self.db.path).parent/'previews'
                    preview_root.mkdir(exist_ok=True)
                    out = Path(tempfile.mkdtemp(prefix='view-',dir=preview_root))/job['name']
                with open(Path(temp)/'error.txt','wb') as err:
                    process = subprocess.Popen([sys.executable,self.worker_script,
                        job['path'],'copy' if job.get('open_copy') else job['kind'],str(out)],stdout=subprocess.DEVNULL,stderr=err,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
                    deadline = time.monotonic()+timeout
                    while process.poll() is None:
                        if self.stop_event.wait(.1) or time.monotonic()>deadline:
                            self.kill_worker(process)
                            if self.stop_event.is_set():
                                raise RuntimeError('終了要求により処理を中断しました。')
                            raise TimeoutError(f'取得・生成が{timeout}秒でタイムアウトしました。再試行するまで保留します。')
                if process.returncode:
                    detail = (Path(temp)/'error.txt').read_text(errors='replace')[-1600:]
                    raise RuntimeError(detail or 'サムネイル生成に失敗しました。')
                st = os.stat(job['path'],follow_symlinks=False)
                if (st.st_size,st.st_mtime_ns) == (job['size'],job['mtime_ns']):
                    if job.get('open_copy'):
                        if not self.stop_event.is_set():
                            self.copy_ready.emit(str(out))
                    else:
                        manifest = json.loads(Path(str(out)+'.json').read_text(encoding='utf-8'))
                        count = manifest['page_count']
                        if not isinstance(count,int) or count<0 or (count==0 and not (job['kind']=='powerpoint' or (job['kind'] in EMPTY_FILE_KINDS and job['size']==0))):
                            raise ValueError('ページ数が不正です。')
                        folder = Path(str(out)+'.pages')
                        self.db.save_thumb(job,out.read_bytes() if count else None,
                            pages=((folder/f'{i}.jpg').read_bytes() for i in range(1,min(count,MAX_PAGE_THUMBNAILS)+1)),page_count=count,
                            metadata=manifest.get('metadata'))
                else:
                    self.request_scan()
            self.message.emit('待機中：サムネイル生成完了')
        except Exception as exc:
            if not self.stop_event.is_set():
                if not job.get('open_copy'):
                    self.db.fail_job(job,exc,timed_out=isinstance(exc,TimeoutError))
                self.message.emit('処理保留: '+job['name']+' / '+str(exc))

    @staticmethod
    def kill_worker(process):
        if os.name == 'nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        else:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)


class LoaderSignals(QObject):
    ready = Signal(object,object)


class ThumbnailModel(QAbstractListModel):
    @staticmethod
    def identity(row):
        return row['id'],row['registration_uid']

    @staticmethod
    def key(row):
        return row['id'],row['registration_uid'],row['revision']

    def __init__(self, db):
        super().__init__()
        self.db, self.items = db, []
        self.cache = OrderedDict()
        self.pending = set()
        self.pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='db-thumbnail')
        self.signals = LoaderSignals()
        self.signals.ready.connect(self.loaded)
        self.positions = {}
        pix = QPixmap(160,110)
        pix.fill(QColor('#e4e8ee'))
        self.placeholder = QIcon(pix)

    def reset(self, rows):
        valid = {self.key(r) for r in rows}
        for key in list(self.cache):
            if key not in valid:
                self.cache.pop(key,None)
        if [self.identity(r) for r in rows] == [self.identity(r) for r in self.items]:
            changed = [i for i,(old,new) in enumerate(zip(self.items,rows)) if old != new]
            self.items = rows
            self.positions = {self.key(r):i for i,r in enumerate(rows)}
            for i in changed:
                index = self.index(i)
                self.dataChanged.emit(index,index)
            return
        self.beginResetModel()
        self.items = rows
        self.positions = {self.key(r):i for i,r in enumerate(rows)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        row = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            mark = tr('［削除候補］') if row['missing'] else tr('［タイムアウト］') if row['timed_out'] else tr('［生成エラー］') if row['error'] else ''
            pages = tr('［{v0}ページ］',v0=row['page_count']) if row.get('page_count',1)>1 else ''
            if row.get('page_count')==0:
                pages = tr('［空ファイル］') if row['size']==0 else tr('［空のPPT］')
            return ('★ ' if row.get('favorite') else '')+mark+pages+row['name']
        if role == Qt.ItemDataRole.ToolTipRole:
            return row['path']+f"\n{row['size']:,} bytes"+ ('\n'+row['error'] if row['error'] else '')
        if role == Qt.ItemDataRole.ForegroundRole and row['missing']:
            return QColor('#b45309')
        if role == Qt.ItemDataRole.DecorationRole:
            key = self.key(row)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if key not in self.pending and len(self.pending)<200:
                self.pending.add(key)
                self.pool.submit(self.load,key)
            return self.placeholder
        return None

    def load(self, key):
        try:
            data = self.db.thumbnail(*key)
        except Exception:
            data = None
        self.signals.ready.emit(key,data)

    def loaded(self, key, data):
        self.pending.discard(key)
        position = self.positions.get(key)
        if position is None or self.key(self.items[position]) != key:
            return  # Completion from a removed registration or older revision.
        pix = QPixmap()
        icon = QIcon(pix) if data and pix.loadFromData(data) else self.placeholder
        self.cache[key] = icon
        while len(self.cache)>600:
            self.cache.popitem(last=False)
        if position is not None:
            index = self.index(position)
            self.dataChanged.emit(index,index,[Qt.ItemDataRole.DecorationRole])


class PageModel(ThumbnailModel):
    @staticmethod
    def identity(row):
        return row['id'],row['registration_uid'],row['page_number']

    @staticmethod
    def key(row):
        return row['id'],row['registration_uid'],row['revision'],row['page_number']

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role==Qt.ItemDataRole.DisplayRole:
            return tr('{v0} ページ',v0=self.items[index.row()]['page_number'])
        return super().data(index,role)

    def load(self,key):
        try:
            blob = self.db.page_thumbnail(*key)
            if blob is None and key[3]==1:
                blob = self.db.thumbnail(*key[:3])
        except Exception:
            blob = None
        self.signals.ready.emit(key,blob)


class PageBadgeDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        row = index.model().items[index.row()]
        count = row.get('page_count',1)
        if count<=1:
            return
        painter.save()
        text = f'{count} p'
        width = option.fontMetrics.horizontalAdvance(text)+16
        rect = QRect(option.rect.right()-width-8,option.rect.top()+6,width,24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#25364b'))
        painter.drawRoundedRect(rect,4,4)
        painter.setPen(QColor('white'))
        painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text)
        painter.restore()


class HoverListView(QListView):
    """Hover reads the existing thumbnail cache only; never opens a source."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.hover_key = None
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self.show_hover)
        self.popup = QLabel(self,Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        self.popup.setMargin(4)
        self.popup.setStyleSheet('QLabel { background: white; border: 1px solid #65758b; }')
        self.popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.pointer_timer = QTimer(self)
        self.pointer_timer.setInterval(100)
        self.pointer_timer.timeout.connect(self.check_pointer)
        self.tiles_signals = LoaderSignals()
        self.tiles_signals.ready.connect(self.tiles_ready)
        self.tiles_pending = None
        self.tiles_cache = OrderedDict()
        self.verticalScrollBar().valueChanged.connect(self.clear_hover)
        self.horizontalScrollBar().valueChanged.connect(self.clear_hover)

    def clear_hover(self,*args):
        self.hover_timer.stop()
        self.pointer_timer.stop()
        self.hover_key = None
        self.popup.hide()

    def eventFilter(self,watched,event):
        if watched is self.viewport():
            if event.type()==QEvent.Type.MouseMove:
                index = self.indexAt(event.position().toPoint())
                key = self.model().key(self.model().items[index.row()]) if index.isValid() else None
                if key!=self.hover_key:
                    self.clear_hover()
                    self.hover_key = key
                    if key:
                        self.hover_timer.start(400)
            elif event.type()==QEvent.Type.Leave:
                # A native popup can synthesize Leave without any cursor movement.
                QTimer.singleShot(0,self.check_pointer)
            elif event.type() in (QEvent.Type.MouseButtonPress,QEvent.Type.Wheel):
                self.clear_hover()
            elif event.type()==QEvent.Type.ToolTip:
                return True
        return super().eventFilter(watched,event)

    def check_pointer(self):
        if self.hover_key is None:
            return
        point = self.viewport().mapFromGlobal(QCursor.pos())
        index = self.indexAt(point)
        key = self.model().key(self.model().items[index.row()]) if index.isValid() else None
        if not self.isVisible() or not self.viewport().rect().contains(point) or key!=self.hover_key:
            self.clear_hover()

    def show_hover(self):
        index = self.indexAt(self.viewport().mapFromGlobal(QCursor.pos()))
        if not index.isValid() or not self.isVisible():
            self.clear_hover()
            return
        key = self.model().key(self.model().items[index.row()])
        if key!=self.hover_key:
            self.clear_hover()
            return
        row = self.model().items[index.row()]
        if row.get('page_count',1)>1 and not isinstance(self.model(),PageModel):
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            request = (key,int(area.width()*.85),int(area.height()*.85))
            if request in self.tiles_cache:
                self.display_popup(QPixmap.fromImage(self.tiles_cache[request]),QCursor.pos())
            elif self.tiles_pending != request:
                self.tiles_pending = request
                self.model().pool.submit(self.build_tiles,request,dict(row))
            return
        icon = self.model().data(index,Qt.ItemDataRole.DecorationRole)
        if key not in self.model().cache:
            self.hover_timer.start(100)
            return
        if icon.cacheKey()==self.model().placeholder.cacheKey():
            return
        self.display_popup(icon.pixmap(QSize(512,512),1.0),QCursor.pos())

    def build_tiles(self,request,row):
        key,width,height = request
        try:
            count = min(row['page_count'],MAX_PAGE_THUMBNAILS)
            columns = max(range(1,count+1),key=lambda c:min(width/c,(height/math.ceil(count/c))-22))
            # Bound every cell, including labels, so large documents never clip pages.
            cellw = max(1,int(width/columns))
            cellh = max(1,int(height/math.ceil(count/columns)))
            margin = min(4,cellw//10,cellh//10)
            label_height = min(20,cellh//5)
            side = max(1,min(512,cellw-2*margin,cellh-2*margin-label_height))
            cellw,cellh = side+2*margin,side+2*margin+label_height
            canvas = QImage(min(width,columns*cellw),min(height,math.ceil(count/columns)*cellh),QImage.Format.Format_RGB32)
            canvas.fill(QColor('white'))
            painter = QPainter(canvas)
            try:
                for page in range(1,count+1):
                    if self.hover_key != key:
                        return
                    blob = self.model().db.page_thumbnail(*key,page)
                    image = QImage.fromData(blob) if blob else QImage()
                    x,y = ((page-1)%columns)*cellw,((page-1)//columns)*cellh
                    if not image.isNull():
                        scaled = image.scaled(side,side,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                        painter.drawImage(x+margin+(side-scaled.width())//2,y+margin+(side-scaled.height())//2,scaled)
                    painter.setPen(QColor('#25364b'))
                    if label_height>=12:
                        painter.drawText(QRect(x,y+side+margin,cellw,label_height),Qt.AlignmentFlag.AlignCenter,str(page))
            finally:
                painter.end()
            self.tiles_signals.ready.emit(request,canvas)
        except Exception:
            self.tiles_signals.ready.emit(request,None)
        finally:
            if self.tiles_pending == request:
                self.tiles_pending = None

    def tiles_ready(self,request,canvas):
        if self.hover_key != request[0] or canvas is None:
            return
        self.tiles_cache[request] = canvas
        while len(self.tiles_cache)>2:
            self.tiles_cache.popitem(last=False)
        self.show_hover()

    def display_popup(self,pixmap,position):
        # adjustSize() caps top-level widgets and can crop a large QLabel pixmap.
        pixmap.setDevicePixelRatio(1.0)
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        padding = 12  # label margin and stylesheet border, both sides
        limit = QSize(max(1,area.width()-padding-8),max(1,area.height()-padding-8))
        if pixmap.width()>limit.width() or pixmap.height()>limit.height():
            pixmap = pixmap.scaled(limit,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.popup.setPixmap(pixmap)
        self.popup.setFixedSize(pixmap.width()+padding,pixmap.height()+padding)
        x = position.x()+20
        y = position.y()+20
        if x+self.popup.width()>area.right():
            x = position.x()-self.popup.width()-20
        if y+self.popup.height()>area.bottom():
            y = position.y()-self.popup.height()-20
        x = max(area.left(),min(x,area.right()-self.popup.width()+1))
        y = max(area.top(),min(y,area.bottom()-self.popup.height()+1))
        self.popup.move(x,y)
        self.popup.show()
        if self.hover_key is not None:
            self.pointer_timer.start()

    def hideEvent(self,event):
        self.clear_hover()
        super().hideEvent(event)


class PagesWindow(QDialog):
    def __init__(self,db,row,parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.db,self.uid = db,row['registration_uid']
        self.setWindowTitle(tr('全ページ — ')+row['name'])
        self.resize(900,700)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.view = HoverListView(self)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setUniformItemSizes(True)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setIconSize(QSize(240,240))
        self.view.setGridSize(QSize(265,280))
        self.model = PageModel(db)
        self.view.setModel(self.model)
        layout.addWidget(self.view)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_pages)
        self.timer.start(1000)
        self.signature = None
        self.refresh_pages()

    def refresh_pages(self):
        row = self.db.file_record(self.uid)
        if row is None:
            self.status.setText(tr('カタログから登録が解除されました。'))
            self.model.reset([])
            self.view.clear_hover()
            return
        signature = (row['revision'],row['page_count'])
        if self.signature!=signature:
            self.signature = signature
            self.view.clear_hover()
            self.model.reset([dict(row,page_number=i) for i in range(1,min(row['page_count'],MAX_PAGE_THUMBNAILS)+1)])
        pending = row['thumb_size'] is None or row['thumb_size']!=row['size'] or row['thumb_mtime']!=row['mtime_ns']
        note = tr(' / 更新待ち（保存済みの画像を表示）') if pending else ''
        if row['timed_out']:
            note = tr(' / タイムアウト保留：メイン画面から再試行してください')
        elif row['error']:
            note = tr(' / 生成エラー：メイン画面の項目で詳細を確認してください')
        self.status.setText(tr('{v0} ページ — ホバーで最大512px表示',v0=row['page_count'])+note)

    def closeEvent(self,event):
        self.timer.stop()
        self.view.clear_hover()
        self.model.pool.shutdown(wait=True,cancel_futures=True)
        super().closeEvent(event)


class DetailsWindow(QWidget):
    saved = Signal()

    def __init__(self, db, row, parent=None):
        super().__init__(parent)
        self.db, self.uid = db, row['registration_uid']
        self.setWindowTitle(tr('メタ情報・メモ — ')+row['name'])
        self.resize(720,720)
        layout = QVBoxLayout(self)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        layout.addWidget(self.info,2)
        self.memo_label = QLabel('ユーザーメモ（DBに保存）')
        layout.addWidget(self.memo_label)
        self.memo = QPlainTextEdit()
        self.previous = row.get('memo','')
        self.memo.setPlainText(self.previous)
        layout.addWidget(self.memo,1)
        self.tags = QLineEdit(row.get('tags',''))
        self.tags.setPlaceholderText('タグ（カンマ区切り）')
        layout.addWidget(self.tags)
        self.favorite = QCheckBox('★ お気に入り')
        self.favorite.setChecked(bool(row.get('favorite',0)))
        layout.addWidget(self.favorite)
        self.previous_annotations = (self.previous,row.get('tags',''),row.get('favorite',0))
        self.status = QLabel()
        layout.addWidget(self.status)
        self.loading = False
        self.memo.textChanged.connect(self.queue_save)
        self.tags.textChanged.connect(self.queue_save)
        self.favorite.toggled.connect(self.queue_save)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.reload_info)
        self.timer.start(1500)
        self.reload_info()

    def dirty(self):
        tags = ', '.join(dict.fromkeys(t.strip() for t in self.tags.text().replace('、',',').split(',') if t.strip()))
        return (self.memo.toPlainText(),tags,int(self.favorite.isChecked())) != self.previous_annotations

    def queue_save(self,*args):
        if self.loading or self.uid is None:
            return
        self.status.setText(tr('未保存（ファイル切り替え・終了時に保存）') if self.dirty() else tr('保存済み'))

    def set_row(self,row):
        self.loading = True
        if row is None:
            self.uid = None
            self.info.setPlainText(tr('ファイルを選択してください。'))
            self.memo.clear()
            self.tags.clear()
            self.favorite.setChecked(False)
            self.previous_annotations = ('','',0)
            self.setEnabled(False)
            self.loading = False
            return
        self.setEnabled(True)
        self.uid = row['registration_uid']
        self.previous = row['memo']
        self.memo.setPlainText(row['memo'])
        self.tags.setText(row['tags'])
        self.favorite.setChecked(bool(row['favorite']))
        self.previous_annotations = (row['memo'],row['tags'],row['favorite'])
        self.status.clear()
        self.loading = False
        self.reload_info()

    def reload_info(self):
        if self.uid is None:
            return
        row = self.db.file_record(self.uid)
        if row is None:
            self.status.setText(tr('登録が解除されました。必要なメモはコピーして保管してください。'))
            return
        try:
            metadata = json.loads(row['metadata_json'])
        except (ValueError,TypeError):
            metadata = {}
        lines = [row['path'], tr('種類: {v0}',v0=row['kind']), tr('サイズ: {v0:,} bytes',v0=row['size']),
                 tr('更新日時: ')+datetime.fromtimestamp(row['mtime_ns']/1e9).isoformat(' ',timespec='seconds'),
                 tr('ページ数: {v0}',v0=row['page_count']), tr('エラー: ')+(translate_message(row['error']) if row['error'] else tr('なし'))]
        if row['page_count']>MAX_PAGE_THUMBNAILS:
            lines.append(tr('サムネイルは先頭20ページまで表示します。'))
        if row['missing']:
            lines.append(tr('状態: 削除候補'))
        if row['thumb_size'] != row['size'] or row['thumb_mtime'] != row['mtime_ns']:
            lines.append(tr('生成待ち：表示中のメタ情報は前回取得分の場合があります。'))
        lines.append(tr('\n画像メタ情報（先頭ページ）'))
        lines.extend(f'{tr(k)}: {translate_message(str(v)) if k in ("状態","EXIF取得エラー") else v}' for k,v in metadata.items())
        if not metadata:
            lines.append(tr('未取得（画像は右クリックの再スキャン・再生成で取得）') if row['kind']=='image' else tr('画像メタ情報の対象外'))
        text = '\n'.join(lines)
        if self.info.toPlainText() != text:
            self.info.setPlainText(text)

    def save(self,automatic=False):
        if self.uid is None:
            return False
        text = self.memo.toPlainText()
        try:
            ok = self.db.save_annotations(self.uid,text,self.tags.text(),self.favorite.isChecked(),self.previous_annotations)
        except Exception as exc:
            self.status.setText(tr('保存失敗: ')+translate_message(str(exc)))
            if not automatic:
                QMessageBox.warning(self,tr('保存失敗'),translate_message(str(exc)))
            return False
        if not ok:
            self.status.setText(tr('未保存：登録解除または編集の競合。入力内容をコピーして保管し、アプリを開き直してください。'))
            if not automatic:
                QMessageBox.warning(self,tr('保存できません'),self.status.text())
            return False
        self.previous = text
        tags = ', '.join(dict.fromkeys(t.strip() for t in self.tags.text().replace('、',',').split(',') if t.strip()))
        self.previous_annotations = (text,tags,int(self.favorite.isChecked()))
        self.status.setText(tr('保存済み'))
        self.saved.emit()
        return True

    def closeEvent(self,event):
        if self.dirty() and not self.save(automatic=True):
            answer = QMessageBox.question(self,tr('未保存のメモ'),tr('メモを保存して閉じますか？'),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel or (answer == QMessageBox.StandardButton.Save and not self.save()):
                event.ignore()
                return
        self.timer.stop()
        event.accept()


class Window(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.data_home = Path(db.path).parent
        set_language(db.get_setting('language','ja'))
        install_dialog_translator()
        self.exiting = False
        self.setWindowTitle('Media Catalog — 画像・動画・PowerPoint')
        self.setWindowIcon(QIcon(str(Path(__file__).with_name('assets')/'media_catalog.ico')))
        self.resize(1180,760)
        self.setStyleSheet(STYLE)
        main = QWidget()
        self.setCentralWidget(main)
        layout = QVBoxLayout(main)
        layout.setContentsMargins(16,16,16,8)
        layout.setSpacing(10)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        for text, callback in [('フォルダ登録',self.add_folder),('今すぐスキャン',self.scan),
                               ('選択を再生成',self.regenerate),('DBバックアップ',self.backup_db)]:
            button = QPushButton(text)
            if text=='フォルダ登録':
                button.setObjectName('primary')
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch()
        options_button = QPushButton('設定・管理')
        options_button.setCheckable(True)
        bar.addWidget(options_button)
        self.language_label = QLabel('言語')
        bar.addWidget(self.language_label)
        self.language_combo = QComboBox()
        self.language_combo.addItem('日本語','ja')
        self.language_combo.addItem('English','en')
        self.language_combo.setCurrentIndex(1 if get_language()=='en' else 0)
        bar.addWidget(self.language_combo)
        filters = QHBoxLayout()
        layout.addLayout(filters)
        self.search = QLineEdit()
        self.search.setPlaceholderText('検索：キーワード、tag:タグ、ext:tif、-除外')
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search,1)
        self.kind_filter = QComboBox()
        self.sort_order = QComboBox()
        for label,value in [('すべての形式',''),('画像','image'),('動画','video'),('PowerPoint','powerpoint'),('SVG','svg'),('Illustrator','illustrator')]:
            self.kind_filter.addItem(label,value)
        for label,value in [('名前順','name'),('更新が新しい順','newest'),('更新が古い順','oldest'),('サイズが大きい順','largest')]:
            self.sort_order.addItem(label,value)
        filters.addWidget(self.kind_filter)
        filters.addWidget(self.sort_order)
        help_button = QPushButton('検索ヘルプ')
        help_button.clicked.connect(self.search_help)
        filters.addWidget(help_button)
        filters = QHBoxLayout()
        layout.addLayout(filters)
        self.favorites = QCheckBox('★ のみ')
        filters.addWidget(self.favorites)
        self.recursive = QCheckBox('サブフォルダを含む')
        self.recursive.setChecked(True)
        filters.addWidget(self.recursive)
        self.missing = QCheckBox('削除候補のみ')
        filters.addWidget(self.missing)
        self.timeouts = QCheckBox('タイムアウトのみ')
        filters.addWidget(self.timeouts)
        self.errors = QCheckBox('エラーのみ')
        filters.addWidget(self.errors)
        filters.addStretch()
        clear_button = QPushButton('絞り込み解除')
        clear_button.clicked.connect(self.clear_filters)
        filters.addWidget(clear_button)
        options = QWidget()
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(0,0,0,0)
        layout.addWidget(options)
        options.hide()
        options_button.toggled.connect(options.setVisible)
        filters = QHBoxLayout()
        options_layout.addLayout(filters)
        self.auto_scan=QCheckBox('定期スキャン')
        self.auto_scan.setChecked(db.get_setting('scan_enabled','1')=='1')
        filters.addWidget(self.auto_scan)
        self.generate = QCheckBox('自動生成（未キャッシュは取得）')
        self.generate.setChecked(db.get_setting('generate','1')=='1')
        filters.addWidget(self.generate)
        filters.addWidget(QLabel('スキャン間隔'))
        self.interval = QSpinBox()
        self.interval.setMaximumWidth(130)
        self.interval.setRange(1,1440)
        self.interval.setSuffix(' 分')
        self.interval.setValue(int(db.get_setting('interval',600))//60)
        filters.addWidget(self.interval)
        settings = QHBoxLayout()
        options_layout.addLayout(settings)
        settings.addWidget(QLabel('取得・生成タイムアウト'))
        self.timeout = QSpinBox()
        self.timeout.setRange(1,1440)
        self.timeout.setSuffix(' 分')
        self.timeout.setValue(int(db.get_setting('timeout',1800))//60)
        settings.addWidget(self.timeout)
        for text, callback in [('表示中のタイムアウトを再試行',self.retry_timeouts),('選択フォルダの登録解除',self.unregister_folder)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            settings.addWidget(button)
        self.pause_button = QPushButton('生成を一時停止')
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self.pause_generation)
        bar.insertWidget(4,self.pause_button)
        remove_button = QPushButton('選択候補をカタログから削除')
        remove_button.clicked.connect(self.remove_selected)
        filters.addWidget(remove_button)
        import_button = QPushButton('メモ・タグの取り込み')
        import_button.clicked.connect(self.import_annotations)
        filters.addWidget(import_button)
        restore_bar=QHBoxLayout()
        options_layout.addLayout(restore_bar)
        for text,callback in [('DB全体を復元',self.restore_catalog),('登録フォルダのパス変更',self.relocate_catalog),('DB保存フォルダを開く',lambda:self.explore(str(Path(self.db.path).parent)))]:
            button=QPushButton(text);button.clicked.connect(callback);restore_bar.addWidget(button)
        restore_bar.addStretch()
        settings.addStretch()
        self.progress_label = QLabel('生成状況を確認中…')
        self.progress_label.setObjectName('progress')
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)
        self.active_jobs = []
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_progress)
        self.stats_timer.start(1000)
        split = self.splitter = QSplitter()
        split.setHandleWidth(8)
        split.setChildrenCollapsible(False)
        layout.addWidget(split,1)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel('登録フォルダ')
        self.tree.setRootIsDecorated(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.folder_menu)
        split.addWidget(self.tree)
        self.view = HoverListView()
        self.view.setItemDelegate(PageBadgeDelegate(self.view))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setBatchSize(150)
        self.view.setUniformItemSizes(True)
        self.view.setIconSize(QSize(180,140))
        self.view.setGridSize(QSize(210,188))
        self.view.setWordWrap(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setMovement(QListView.Movement.Static)
        self.model = ThumbnailModel(db)
        self.view.setModel(self.model)
        self.model.modelAboutToBeReset.connect(self.view.clear_hover)
        self.view.doubleClicked.connect(self.open_file)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.file_menu)
        split.addWidget(self.view)
        empty = dict(registration_uid=None,name='',memo='',tags='',favorite=0)
        self.details = DetailsWindow(db,empty,self)
        self.details.set_row(None)
        split.addWidget(self.details)
        split.setSizes([220,650,360])
        self.details.setMinimumWidth(180)
        self.details_toggle = QCheckBox('詳細パネル')
        settings.addWidget(self.details_toggle)
        self.details_toggle.setChecked(db.get_setting('details_visible','1')=='1')
        self.details.setVisible(self.details_toggle.isChecked())
        self.details_toggle.toggled.connect(self.toggle_details)
        try:
            sizes = json.loads(db.get_setting('panel_sizes','[220,650,360]'))
            if len(sizes)==3 and all(isinstance(n,int) and n>0 for n in sizes):
                split.setSizes(sizes)
        except (ValueError,TypeError):
            pass
        split.splitterMoved.connect(self.save_panel_sizes)
        self.view.selectionModel().currentChanged.connect(self.selection_changed)
        self.count = QLabel()
        self.statusBar().addPermanentWidget(self.count)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        self.details.saved.connect(self.schedule_refresh)
        self.favorites.toggled.connect(self.schedule_refresh)
        self.search.textChanged.connect(self.schedule_refresh)
        self.kind_filter.currentIndexChanged.connect(self.schedule_refresh)
        self.sort_order.currentIndexChanged.connect(self.schedule_refresh)
        shortcut = QAction(self)
        shortcut.setShortcut('Ctrl+F')
        shortcut.triggered.connect(self.search.setFocus)
        self.addAction(shortcut)
        self.recursive.toggled.connect(self.schedule_refresh)
        self.missing.toggled.connect(self.schedule_refresh)
        self.timeouts.toggled.connect(self.schedule_refresh)
        self.errors.toggled.connect(self.schedule_refresh)
        self.tree.currentItemChanged.connect(self.schedule_refresh)
        self.service = Service(db)
        self.pause_button.setChecked(self.service.paused)
        self.service.changed.connect(self.schedule_refresh)
        self.service.progress.connect(self.receive_progress)
        self.service.message.connect(lambda msg:self.statusBar().showMessage(translate_message(msg)))
        self.service.copy_ready.connect(self.open_local_copy)
        self.generate.toggled.connect(self.settings_changed)
        self.auto_scan.toggled.connect(self.settings_changed)
        self.interval.valueChanged.connect(self.settings_changed)
        self.timeout.valueChanged.connect(self.settings_changed)
        self.tray = QSystemTrayIcon(self.windowIcon(),self)
        self.tray.setToolTip('Media Catalog')
        menu = QMenu(self)
        for text, callback in [('開く',self.show_window),('今すぐスキャン',self.scan),('終了',self.quit_app)]:
            action = QAction(text,self)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.show_window() if reason==QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.retranslate_ui(refresh=False)
        self.language_combo.currentIndexChanged.connect(self.change_language)
        self.refresh()
        self.service.start()

    def clear_filters(self):
        self.search.clear()
        self.kind_filter.setCurrentIndex(0)
        for checkbox in (self.favorites,self.missing,self.timeouts,self.errors):
            checkbox.setChecked(False)
        self.schedule_refresh()

    def search_help(self):
        QMessageBox.information(self,tr('検索ヘルプ'),tr('スペース区切りはAND検索、"引用符"はフレーズ検索、-語句は除外です。\nname:名前 memo:メモ tag:タグ path:パス meta:メタ情報 ext:tif type:image\n例：細胞 tag:実験 -失敗 ext:tif\n検索は保存済みDBのみを参照します。Ctrl+Fで検索欄へ移動します。'))

    def schedule_refresh(self,*args):
        # Throttle, rather than restart, to avoid starving updates during generation.
        if not self.refresh_timer.isActive():
            self.refresh_timer.start(350)

    @staticmethod
    def _translate_property(obj, property_name, getter, setter):
        source = obj.property(property_name)
        if source is None:
            source = static_source(getter())
            obj.setProperty(property_name,source)
        setter(tr(source))

    def retranslate_ui(self,refresh=True):
        self.setWindowTitle(tr('Media Catalog — 画像・動画・PowerPoint'))
        self.view.clear_hover()
        for widget in self.findChildren(QWidget):
            if isinstance(widget,(QLabel,QPushButton,QCheckBox)) and widget not in (self.view.popup,self.details.status,self.count,self.progress_label,self.pause_button):
                self._translate_property(widget,'i18nText',widget.text,widget.setText)
            if isinstance(widget,QLineEdit):
                self._translate_property(widget,'i18nPlaceholder',widget.placeholderText,widget.setPlaceholderText)
            if isinstance(widget,QSpinBox):
                self._translate_property(widget,'i18nSuffix',widget.suffix,widget.setSuffix)
        for action in self.findChildren(QAction):
            self._translate_property(action,'i18nText',action.text,action.setText)
        self.tree.setHeaderLabel(tr('登録フォルダ'))
        for combo in (self.kind_filter,self.sort_order):
            for index in range(combo.count()):
                source = combo.itemData(index,Qt.ItemDataRole.UserRole+1)
                if source is None:
                    source = combo.itemText(index)
                    combo.setItemData(index,source,Qt.ItemDataRole.UserRole+1)
                combo.setItemText(index,tr(source))
        if self.details.uid:
            self.details.reload_info()
            self.details.status.setText(tr(static_source(self.details.status.text())))
        else:
            self.details.info.setPlainText(tr('ファイルを選択してください。'))
        self.pause_button.setText(tr('生成を再開') if self.service.paused else tr('生成を一時停止'))
        self.update_progress()
        if refresh:
            self._tree_signature = None
            self.refresh()
            if self.model.rowCount():
                self.model.dataChanged.emit(self.model.index(0),self.model.index(self.model.rowCount()-1))

    def change_language(self,index):
        language = self.language_combo.itemData(index)
        set_language(language)
        self.db.set_setting('language',language)
        install_dialog_translator()
        self.statusBar().clearMessage()
        self.retranslate_ui()

    def save_panel_sizes(self,*args):
        if self.details.isVisible():
            self.db.set_setting('panel_sizes',json.dumps(self.splitter.sizes()))

    def toggle_details(self,visible):
        if not visible:
            self.save_panel_sizes()
        self.details.setVisible(visible)
        self.db.set_setting('details_visible',int(visible))
        if visible:
            self.splitter.setSizes(json.loads(self.db.get_setting('panel_sizes','[220,650,360]')))

    def folder(self):
        item = self.tree.currentItem()
        return item.data(0,Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        folder = self.folder()
        selected = {i for i in self.selected_ids()}
        roots = self.db.roots()
        folders = self.db.folders()
        signature = ([(r['path'],r['error']) for r in roots],folders)
        if getattr(self,'_tree_signature',None) != signature:
            expanded = set()
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                if item.isExpanded():
                    expanded.add(item.data(0,Qt.ItemDataRole.UserRole))
                it += 1
            self._tree_signature = signature
            self.tree.blockSignals(True)
            self.tree.clear()
            all_item = QTreeWidgetItem(self.tree,[tr('すべて')])
            all_item.setData(0,Qt.ItemDataRole.UserRole,None)
            nodes = {}
            for root in roots:
                label = root['path']+ (tr(' ［確認不能］') if root['error'] else '')
                item = QTreeWidgetItem(self.tree,[label])
                item.setData(0,Qt.ItemDataRole.UserRole,root['path'])
                item.setToolTip(0,root['error'] or root['path'])
                nodes[root['path']] = item
            for path in folders:
                root = next((r['path'] for r in roots if within(path,r['path'])),None)
                if root is None:
                    continue
                current = root
                rel = os.path.relpath(path,root)
                if rel == '.':
                    continue
                for part in Path(rel).parts:
                    child = norm(os.path.join(current,part))
                    if child not in nodes:
                        node = QTreeWidgetItem(nodes[current],[part])
                        node.setData(0,Qt.ItemDataRole.UserRole,child)
                        nodes[child] = node
                    current = child
            self.tree.setCurrentItem(nodes.get(folder,all_item))
            for path,item in nodes.items():
                item.setExpanded(path in expanded or any(path==r['path'] for r in roots))
            self.tree.blockSignals(False)
            folder = self.folder()
        rows = self.db.rows(folder,self.recursive.isChecked(),self.missing.isChecked(),self.search.text(),self.timeouts.isChecked(),self.errors.isChecked(),kind=self.kind_filter.currentData(),sort=self.sort_order.currentData())
        if self.favorites.isChecked():
            rows = [r for r in rows if r['favorite']]
        self.view.selectionModel().blockSignals(True)
        self.model.reset(rows)
        from PySide6.QtCore import QItemSelectionModel
        for i,row in enumerate(rows):
            if row['id'] in selected:
                self.view.selectionModel().select(self.model.index(i),QItemSelectionModel.SelectionFlag.Select)
        if self.details.uid:
            current = next((i for i,r in enumerate(rows) if r['registration_uid']==self.details.uid),None)
            if current is not None:
                self.view.selectionModel().setCurrentIndex(self.model.index(current),QItemSelectionModel.SelectionFlag.NoUpdate)
            elif not self.details.dirty():
                self.details.set_row(None)
        self.view.selectionModel().blockSignals(False)
        self.count.setText(tr('{v0:,} 件 / 削除候補 {v1:,} / タイムアウト {v2:,}',v0=len(rows),v1=sum(r['missing'] for r in rows),v2=sum(r['timed_out'] for r in rows)))

    def settings_changed(self,*args):
        self.service.scan_enabled=self.auto_scan.isChecked()
        self.db.set_setting('scan_enabled',int(self.service.scan_enabled))
        self.service.interval = self.interval.value()*60
        self.service.generate = self.generate.isChecked()
        self.db.set_setting('interval',self.service.interval)
        self.db.set_setting('generate',int(self.service.generate))
        self.service.timeout = self.timeout.value()*60
        self.db.set_setting('timeout',self.service.timeout)
        self.service.wake.set()

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self,tr('登録するフォルダ'))
        if folder:
            try:
                message = self.db.add_root(folder)
                self.refresh()
                self.scan()
                self.statusBar().showMessage(translate_message(message))
            except Exception as exc:
                QMessageBox.warning(self,tr('登録できません'),translate_message(str(exc)))

    def scan(self):
        self.service.request_scan()
        self.statusBar().showMessage(tr('スキャンを予約しました。取得・生成とは独立して実行します。'))

    def selected_ids(self):
        return [self.model.items[i.row()]['id'] for i in self.view.selectedIndexes()]

    def regenerate(self):
        self.db.regenerate(self.selected_ids())
        self.service.wake.set()
        self.schedule_refresh()

    def remove_selected(self):
        candidates = [self.model.items[i.row()] for i in self.view.selectedIndexes() if self.model.items[i.row()]['missing']]
        if not candidates:
            QMessageBox.information(self,tr('削除候補'),tr('削除候補の項目を選択してください。Ctrl+Aで表示中の全件を選択できます。'))
            return
        prompt = QMessageBox(self)
        prompt.setWindowTitle(tr('カタログから削除'))
        prompt.setText(tr('{v0}件のファイル情報・サムネイル・メタ情報・メモ・タグ・お気に入りをカタログから削除しますか？',v0=len(candidates)))
        prompt.setInformativeText(tr('元ファイルには操作しません。削除前に存在を再確認します。'))
        prompt.setDetailedText('\n'.join(r['path'] for r in candidates))
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel)
        prompt.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if prompt.exec()==QMessageBox.StandardButton.Yes:
            removed, restored, errors = self.db.confirm_removal([r['id'] for r in candidates])
            QMessageBox.information(self,tr('処理結果'),tr('カタログから削除: {v0}件\n再出現: {v1}件\n確認不能で保留: {v2}件',v0=removed,v1=restored,v2=len(errors)))
            self.refresh()

    def open_file(self,index):
        row = self.model.items[index.row()]
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(row['path'])):
            QMessageBox.warning(self,tr('開けません'),tr('元ファイルの場所と既定アプリを確認してください。'))

    def explore(self,folder):
        try:
            if os.name=='nt':
                subprocess.Popen(['explorer.exe',os.path.normpath(folder)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except OSError as exc:
            QMessageBox.warning(self,tr('フォルダを開けません'),translate_message(str(exc)))

    def selection_changed(self,current,previous):
        if self.details.dirty() and not self.details.save():
            self.view.selectionModel().blockSignals(True)
            self.view.setCurrentIndex(previous)
            self.view.selectionModel().blockSignals(False)
            return
        row = self.model.items[current.row()] if current.isValid() else None
        self.details.set_row(self.db.file_record(row['registration_uid']) if row else None)

    def pause_generation(self,paused):
        self.service.paused = paused
        self.db.set_setting('paused',int(paused))
        self.service.wake.set()
        self.pause_button.setText(tr('生成を再開') if paused else tr('生成を一時停止'))
        self.update_progress()

    def receive_progress(self,jobs):
        self.active_jobs = jobs

    def update_progress(self):
        counts = self.db.generation_counts()
        state = tr('一時停止（処理中は完了まで継続）') if self.service.paused else (tr('自動生成OFF') if not self.service.generate else tr('生成中'))
        self.progress_label.setText(tr('{v0} | 完了 {v1} / 全体 {v2} | 未完了 {v3} | タイムアウト {v4} | エラー {v5} | 処理中 {v6}\n',
            v0=state,v1=counts['complete'],v2=counts['total'],v3=counts['pending'],v4=counts['timeout'],v5=counts['errors'],v6=len(self.active_jobs))+
            ' / '.join(tr('{v0} ({v1}秒)',v0=name,v1=seconds) for name,seconds in self.active_jobs))

    def restore_catalog(self):
        path,_=QFileDialog.getOpenFileName(self,tr('DB全体を復元'),'', 'SQLite (*.sqlite3 *.sqlite *.db);;All files (*)')
        if path:self.prepare_catalog_switch(path)

    def relocate_catalog(self):
        self.prepare_catalog_switch(self.db.path)

    def prepare_catalog_switch(self,path):
        if getattr(self,'backup_running',False):
            QMessageBox.information(self,tr('保存中'),tr('バックアップ完了後に終了してください。'));return
        if self.details.dirty() and not self.details.save():return
        try:
            from restore_dialog import RestoreDialog
            dialog=RestoreDialog(path,self.data_home,self)
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            self.pending_catalog=dialog.result_path
            QMessageBox.information(self,tr('復元の準備完了'),tr('終了後にもう一度アプリを起動してください。以前のDB: {v0}',v0=self.db.path))
            self.quit_app()
        except Exception as exc:
            QMessageBox.warning(self,tr('復元失敗'),translate_message(str(exc)))

    def import_annotations(self):
        if self.details.dirty() and not self.details.save():
            return
        path,_=QFileDialog.getOpenFileName(self,tr('メモ・タグの取り込み'),'', 'SQLite (*.sqlite3 *.sqlite *.db);;All files (*)')
        if not path:
            return
        try:
            from import_dialog import ImportDialog
            dialog=ImportDialog(self.db,path,self)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                if self.details.uid:
                    self.details.set_row(self.db.file_record(self.details.uid))
                self.refresh()
        except Exception as exc:
            QMessageBox.warning(self,tr('取り込み失敗'),translate_message(str(exc)))

    def backup_db(self):
        if getattr(self,'backup_running',False):
            self.statusBar().showMessage(tr('バックアップの保存中です。'))
            return
        if self.details.dirty() and not self.details.save():
            return
        path,_ = QFileDialog.getSaveFileName(self,tr('DBを新しいファイルに保存'),
            'MediaCatalog-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3','SQLite (*.sqlite3)')
        if not path:
            return
        self.backup_running = True
        self.backup_signals = LoaderSignals()
        self.backup_signals.ready.connect(self.backup_finished)
        self.backup_pool = ThreadPoolExecutor(max_workers=1)
        self.statusBar().showMessage(tr('DBバックアップを保存中…'))
        def run():
            try:
                self.db.backup(path)
                self.backup_signals.ready.emit(path,None)
            except Exception as exc:
                self.backup_signals.ready.emit(path,str(exc))
        self.backup_pool.submit(run)

    def backup_finished(self,path,error):
        self.backup_running = False
        self.backup_pool.shutdown(wait=False)
        if error:
            QMessageBox.warning(self,tr('バックアップ失敗'),translate_message(error)+tr('\n既存ファイルは上書きしません。別の名前を指定してください。'))
        else:
            QMessageBox.information(self,tr('バックアップ完了'),path)

    def file_menu(self,position):
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        row = dict(self.model.items[index.row()])
        menu = QMenu(self)
        open_action = menu.addAction(tr('元ファイルを開く'))
        explore_action = menu.addAction(tr('エクスプローラーで保存フォルダを開く'))
        retry_action = menu.addAction(tr('再スキャン・再生成'))
        chosen = menu.exec(self.view.viewport().mapToGlobal(position))
        if chosen==open_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(row['path']))
        elif chosen==explore_action:
            self.explore(row['parent'])
        elif chosen==retry_action:
            self.db.regenerate([row['id']])
            self.scan()
            self.schedule_refresh()

    def folder_menu(self,position):
        item = self.tree.itemAt(position)
        if item is None or item.data(0,Qt.ItemDataRole.UserRole) is None:
            return
        self.tree.setCurrentItem(item)
        path = item.data(0,Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        explore_action = menu.addAction(tr('エクスプローラーで開く'))
        remove_action = menu.addAction(tr('このフォルダ以下の登録を解除'))
        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen==explore_action:
            self.explore(path)
        elif chosen==remove_action:
            self.unregister_folder()

    def unregister_folder(self):
        folder = self.folder()
        if not folder:
            QMessageBox.information(self,tr('登録解除'),tr('ツリーでフォルダを選択してください。'))
            return
        answer = QMessageBox.question(self,tr('フォルダの登録解除'),
            tr('{v0}\n\nこのフォルダ以下の登録・サムネイル・メタ情報・メモ・タグ・お気に入りをカタログから除去します。元ファイルは削除しません。',v0=folder),
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:
            count = self.db.remove_folder(folder)
            self.refresh()
            self.statusBar().showMessage(tr('{v0}件の登録を解除しました。再登録するまでスキャン対象から外します。',v0=count))

    def retry_timeouts(self):
        ids = [r['id'] for r in self.model.items if r['timed_out'] and not r['missing']]
        self.db.regenerate(ids)
        self.scan()
        self.schedule_refresh()
        self.statusBar().showMessage(tr('{v0}件を再試行予約しました。自動生成ONで順次取得・生成します。',v0=len(ids)))

    def open_local_copy(self,path):
        if self.exiting:
            return
        preview_root = Path(self.db.path).parent/'previews'
        if not within(path,preview_root):
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self,tr('開けません'),tr('作業用コピーを開く既定アプリを確認してください。'))

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self,event):
        if self.exiting:
            event.accept()
        elif self.tray.isVisible():
            if self.details.dirty() and not self.details.save():
                event.ignore()
                return
            self.hide()
            event.ignore()
            self.tray.showMessage('Media Catalog',tr('トレイで監視を継続します。終了はトレイメニューから選択してください。'))
        else:
            event.ignore()
            self.quit_app()

    def quit_app(self):
        if self.exiting:
            return
        if getattr(self,'backup_running',False):
            QMessageBox.information(self,tr('保存中'),tr('バックアップ完了後に終了してください。'))
            return
        for window in self.findChildren(DetailsWindow):
            if not window.close():
                return
        self.exiting = True
        self.setEnabled(False)
        self.statusBar().showMessage(tr('終了処理中…'))
        self.refresh_timer.stop()
        self.stats_timer.stop()
        self.service.stop()
        # Keep Qt running until worker has stopped; never destroy a live QThread.
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.timeout.connect(self.finish_quit)
        self.shutdown_timer.start(100)

    def finish_quit(self):
        if not self.service.isRunning():
            self.shutdown_timer.stop()
            if getattr(self,'pending_catalog',None):
                try:
                    from catalog_restore import select_catalog
                    select_catalog(self.data_home,self.pending_catalog)
                except Exception as exc:
                    QMessageBox.warning(self,tr('復元失敗'),translate_message(str(exc)))
            for window in self.findChildren(PagesWindow):
                window.close()
            self.view.clear_hover()
            self.model.pool.shutdown(wait=True,cancel_futures=True)
            self.tray.hide()
            QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('MediaCatalog')
    app.setWindowIcon(QIcon(str(Path(__file__).with_name('assets')/'media_catalog.ico')))
    if os.name=='nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MediaCatalog.Desktop')
    app.setOrganizationName('MediaCatalog')
    app.setQuitOnLastWindowClosed(False)
    data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data.mkdir(parents=True,exist_ok=True)
    lock = QLockFile(str(data/'application.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None,'Media Catalog',tr('すでに起動しています。タスクトレイをご確認ください。'))
        return 0
    from catalog_restore import selected_catalog
    try:db = Catalog(selected_catalog(data))
    except Exception as exc:
        QMessageBox.warning(None,'Media Catalog',str(exc));return 1
    window = Window(db)
    window.data_home=data
    if '--tray' not in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
