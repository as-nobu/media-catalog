import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect, QPoint, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from screen_capture import compose_selection, ScreenCapture, CaptureOverlay


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def solid(self,w,h,color):
        image = QImage(w,h,QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def test_negative_origins_mixed_dpi_and_boundary(self):
        frames = [(QRect(-100,0,100,100),self.solid(100,100,'red')),
                  (QRect(0,0,100,100),self.solid(200,200,'blue'))]
        result = compose_selection(frames,QRect(-20,20,40,40))
        self.assertEqual((result.width(),result.height()),(80,80))
        self.assertEqual(result.pixelColor(10,20),QColor('red'))
        self.assertEqual(result.pixelColor(70,20),QColor('blue'))
        self.assertTrue(compose_selection(frames,QRect(500,500,20,20)).isNull())

    def test_pixel_coordinates(self):
        image = self.solid(200,200,'black')
        image.setPixelColor(60,80,QColor('green'))
        result = compose_selection([(QRect(100,100,100,100),image)],QRect(130,140,10,10))
        self.assertEqual(result.pixelColor(0,0),QColor('green'))
        self.assertEqual(result.width(),20)

    def test_cancel_and_restore(self):
        parent = QWidget(); parent.show()
        capture = ScreenCapture(parent)
        calls = []
        capture.cancelled.connect(lambda:calls.append('cancel'))
        with patch('screen_capture.QTimer.singleShot'):
            capture.start()
        self.assertFalse(parent.isVisible())
        capture.finish()
        capture.finish()
        self.assertTrue(parent.isVisible())
        self.assertEqual(calls,['cancel'])
        parent.close()

    def test_overlay_drag_and_escape(self):
        capture = ScreenCapture()
        image = self.solid(200,150,'red')
        capture.frames = [(QRect(0,0,200,150),image)]
        overlay = CaptureOverlay(capture,QRect(0,0,200,150),image)
        capture.overlays = [overlay]
        values = []
        capture.captured.connect(values.append)
        overlay.show()
        QTest.mousePress(overlay,Qt.MouseButton.LeftButton,pos=QPoint(20,30))
        QTest.mouseMove(overlay,QPoint(100,110))
        QTest.mouseRelease(overlay,Qt.MouseButton.LeftButton,pos=QPoint(100,110))
        self.assertEqual(len(values),1)
        self.assertEqual(values[0].pixelColor(1,1),QColor('red'))
        self.assertTrue(capture.finished)

    def test_window_manager_close_restores_parent(self):
        parent = QWidget(); parent.show()
        capture = ScreenCapture(parent)
        with patch('screen_capture.QTimer.singleShot'): capture.start()
        overlay = CaptureOverlay(capture,QRect(0,0,200,150),self.solid(200,150,'blue'))
        capture.overlays = [overlay]
        overlay.show()
        overlay.close()
        self.assertTrue(capture.finished)
        self.assertTrue(parent.isVisible())
        parent.close()


if __name__=='__main__': unittest.main()
