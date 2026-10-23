"""Qt screen-region picker. Screenshots live in memory, never in source folders."""
from __future__ import annotations

import math
from PySide6.QtCore import QObject, Signal, Qt, QRect, QRectF, QPoint, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import QApplication, QWidget
from i18n import tr


def compose_selection(frames, rectangle):
    """frames: (logical desktop QRect, physical-pixel QImage) per monitor.

    Use each image's actual dimensions (including mixed DPI and negative
    screen origins). Resample intersecting pieces onto one bounded canvas.
    """
    pieces = [(geometry, image, geometry.intersected(rectangle))
              for geometry, image in frames if geometry.intersects(rectangle)]
    if not pieces:
        return QImage()
    bounds = QRect()
    for _, _, part in pieces:
        bounds = bounds.united(part)
    scale = max(max(image.width()/geometry.width(), image.height()/geometry.height())
                for geometry, image, _ in pieces)
    # Bound large multi-monitor selections to 16 MP before allocating.
    scale = min(scale, math.sqrt(16_000_000/max(1, bounds.width()*bounds.height())))
    result = QImage(max(1, int(bounds.width()*scale)), max(1, int(bounds.height()*scale)),
                    QImage.Format.Format_RGB32)
    result.fill(QColor('black'))
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for geometry, image, part in pieces:
        sx, sy = image.width()/geometry.width(), image.height()/geometry.height()
        source = QRectF((part.x()-geometry.x())*sx, (part.y()-geometry.y())*sy,
                        part.width()*sx, part.height()*sy)
        target = QRectF((part.x()-bounds.x())*scale, (part.y()-bounds.y())*scale,
                        part.width()*scale, part.height()*scale)
        painter.drawImage(target, image, source)
    painter.end()
    return result


class CaptureOverlay(QWidget):
    def __init__(self, controller, geometry, image):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.controller, self.frame = controller, image
        self.setGeometry(geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.frame)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 105))
        selection = self.controller.rectangle.translated(-self.geometry().topLeft())
        if not selection.isEmpty():
            painter.save()
            painter.setClipRect(selection)
            painter.drawImage(self.rect(), self.frame)
            painter.restore()
            painter.setPen(QPen(QColor('#40aaff'), 2))
            painter.drawRect(selection)
        painter.setPen(QColor('white'))
        painter.fillRect(QRect(12, 12, min(650, self.width()-24), 42), QColor(20, 25, 35, 220))
        painter.drawText(QRect(24, 12, self.width()-48, 42), Qt.AlignmentFlag.AlignVCenter,
                         tr('検索する画像をドラッグで選択 / Esc・右クリックでキャンセル'))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.controller.finish()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.controller.origin = event.globalPosition().toPoint()
            self.controller.update_selection(self.controller.origin)

    def mouseMoveEvent(self, event):
        if self.controller.origin is not None:
            self.controller.update_selection(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.controller.origin is None:
            return
        self.controller.update_selection(event.globalPosition().toPoint())
        rect = self.controller.rectangle
        if rect.width() >= 8 and rect.height() >= 8:
            self.controller.finish(compose_selection(self.controller.frames, rect))
        else:
            self.controller.origin = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.controller.finish()

    def closeEvent(self, event):
        # Alt+F4 / window-manager close must also restore hidden app windows.
        if not self.controller.finished:
            self.controller.finish()
        event.accept()


class ScreenCapture(QObject):
    captured = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.overlays = []
        self.frames = []
        self.hidden = []
        self.origin = None
        self.rectangle = QRect()
        self.finished = False

    def start(self):
        self.hidden = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
        for window in self.hidden:
            window.hide()
        # Capture after the compositor has removed this app's windows, and
        # before showing overlays (so the dimming/selection is never captured).
        QTimer.singleShot(250, self.grab)

    def grab(self):
        if self.finished:
            return
        try:
            for screen in QGuiApplication.screens():
                pixmap = screen.grabWindow(0)
                if pixmap.isNull():
                    raise RuntimeError(tr('画面を取得できません。画面キャプチャの許可を確認してください。'))
                image = pixmap.toImage()
                image.setDevicePixelRatio(1.)
                self.frames.append((screen.geometry(), image))
            if not self.frames:
                raise RuntimeError(tr('画面が見つかりません。'))
            for geometry, image in self.frames:
                overlay = CaptureOverlay(self, geometry, image)
                self.overlays.append(overlay)
                overlay.show()
                overlay.raise_()
            self.overlays[0].activateWindow()
            self.overlays[0].setFocus()
        except Exception as exc:
            self.finish(error=str(exc))

    def update_selection(self, point):
        self.rectangle = QRect(self.origin, point).normalized()
        for overlay in self.overlays:
            overlay.update()

    def finish(self, image=None, error=None):
        if self.finished:
            return
        self.finished = True
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()
        self.frames.clear()
        for window in self.hidden:
            window.show()
        self.hidden.clear()
        if self.parent() is not None:
            self.parent().raise_()
            self.parent().activateWindow()
        if error:
            self.failed.emit(error)
        elif image is not None and not image.isNull():
            self.captured.emit(image)
        else:
            self.cancelled.emit()
