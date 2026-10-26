"""Display-only enhancement, async toggles, previews and saved-data isolation."""
import io
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import numpy.testing
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QImage, QCursor
from PySide6.QtWidgets import QApplication
from app import Window, Service, ThumbnailModel, PageModel, HoverListView, PagesWindow
from catalog import Catalog
from display_brightness import auto_brightness, saved_strength
from i18n import set_language


def dark_blob():
    image = Image.new('RGB',(128,96),'black')
    draw = ImageDraw.Draw(image)
    draw.rectangle((20,20,100,80),fill=(5,20,10))
    draw.rectangle((110,85,120,87),fill='white')
    out = io.BytesIO(); image.save(out,'PNG')
    return out.getvalue()


def rgba(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:, :image.width()*4].reshape(image.height(),image.width(),4).copy()


class ToneTests(unittest.TestCase):
    def test_strength_zero_is_exact_and_increasing_strength_is_monotonic(self):
        source = QImage.fromData(dark_blob())
        np.testing.assert_array_equal(rgba(auto_brightness(source,0)),rgba(source))
        previous = rgba(source)
        levels = []
        for strength in [1,25,50,75,100]:
            current = rgba(auto_brightness(source,strength))
            self.assertTrue(np.all(current >= previous))
            levels.append(int(current[50,50,1]))
            previous = current
        self.assertEqual(levels,sorted(set(levels)))
        self.assertGreater(levels[-1],levels[2])

    def test_dark_fluorescence_with_bright_label_and_channel_ratios(self):
        source = QImage.fromData(dark_blob())
        before = rgba(source)
        result = rgba(auto_brightness(source))
        self.assertGreater(result[50,50,1],35)
        self.assertLess(result[50,50,1],80)  # A modest lift, not the old ~119.
        np.testing.assert_allclose(result[50,50,:3] / result[50,50,1], [.25,1,.5], atol=.02)
        np.testing.assert_array_equal(result[0,0], before[0,0])
        np.testing.assert_array_equal(result[86,115], before[86,115])
        np.testing.assert_array_equal(rgba(source),before)

    def test_null_black_and_bright_images_unchanged(self):
        self.assertTrue(auto_brightness(QImage()).isNull())
        for color in ['black','white','red','lime','blue','#e0e0e0']:
            image = QImage(19,13,QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            np.testing.assert_array_equal(rgba(image),rgba(auto_brightness(image)))

    def test_alpha_hidden_pixels_and_gain_bound(self):
        image = QImage(17,9,QImage.Format.Format_RGBA8888)
        image.fill(QColor(1,2,3,127))
        image.setPixelColor(2,2,QColor(255,255,255,0))
        image.setDevicePixelRatio(2)
        result = auto_brightness(image)
        self.assertEqual(result.devicePixelRatio(),2)
        self.assertEqual(result.pixelColor(2,2),image.pixelColor(2,2))
        self.assertEqual(result.pixelColor(5,5).alpha(),127)
        self.assertLessEqual(result.pixelColor(5,5).blue(),9)
        self.assertGreater(result.pixelColor(5,5).blue(),3)

    def test_grayscale_odd_width_and_monotonic_intensities(self):
        image = QImage(37,20,QImage.Format.Format_Grayscale8)
        image.fill(0)
        for x in range(37):
            for y in range(20): image.setPixelColor(x,y,QColor(x,x,x))
        result = rgba(auto_brightness(image))
        self.assertTrue(np.all(np.diff(result[0,:,0].astype(int)) >= 0))
        np.testing.assert_array_equal(result[:,:,0],result[:,:,1])
        np.testing.assert_array_equal(result[:,:,1],result[:,:,2])


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self,predicate,seconds=8):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.app.processEvents()
            if predicate(): return
            time.sleep(.005)
        self.fail('GUI deadline')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        root = base/'cloud'; root.mkdir()
        source = root/'dark.tif'; source.write_bytes(b'placeholder')
        self.db = Catalog(base/'local'/'catalog.sqlite3')
        self.db.set_setting('scan_enabled','0')
        self.db.set_setting('generate','0')
        self.db.add_root(root);self.db.scan(self.db.roots()[0])
        row = self.db.rows()[0]
        self.blob = dark_blob()
        self.db.save_thumb(row,self.blob,pages=[self.blob,self.blob],page_count=2)
        self.row = self.db.rows()[0]
        with self.db.connect() as c:
            c.execute('''INSERT INTO visual_features VALUES(?,1,'visual-v1:dinov2:test',?,?,?,NULL,'')''',
                      (self.row['id'],self.row['registration_uid'],self.row['revision'],b'unchanged-vector'))
            c.execute('''INSERT INTO visual_local_features VALUES(?,1,'sift-v1',?,?,?)''',
                      (self.row['id'],self.row['registration_uid'],self.row['revision'],b'unchanged-sift'))
        source.unlink()  # Every display below must work with the original absent.
        self.models=[]
        self.views=[]

    def tearDown(self):
        for view in self.views: view.close()
        for model in self.models: model.pool.shutdown(wait=True,cancel_futures=True)
        self.app.processEvents()
        set_language('ja')

    def make_model(self,page=False):
        model = (PageModel if page else ThumbnailModel)(self.db)
        model.reset([dict(self.row,page_number=2)] if page else [self.row])
        self.models.append(model)
        return model

    def icon_image(self,model):
        key=model.key(model.items[0])
        model.data(model.index(0),Qt.ItemDataRole.DecorationRole)
        self.wait(lambda:key in model.cache)
        return model.cache[key].pixmap(QSize(512,512),1.0).toImage()

    def snapshot(self):
        with self.db.connect() as c:
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table:[tuple(r) for r in c.execute('SELECT * FROM '+table+(' WHERE key NOT IN (\'display_auto_brightness\',\'display_brightness_strength\')' if table=='settings' else ''))] for table in tables}

    def test_icons_pages_search_results_toggle_without_database_mutation(self):
        before = self.snapshot()
        models=[self.make_model(),self.make_model(page=True),self.make_model()]
        models[2].reset([dict(self.row,search_page=2,search_rank=1)])
        with patch('builtins.open',side_effect=AssertionError('Unexpected file access')), patch('os.stat',side_effect=AssertionError('Unexpected metadata access')):
            for model in models:
                original = rgba(self.icon_image(model))
                self.assertEqual(original[50,50,1],20)
                model.set_brightness_strength(50)
                self.assertGreater(rgba(self.icon_image(model))[50,50,1],35)
                model.set_brightness_strength(0)
                # Avoid NumPy test helpers' lazy imports inside the source-I/O
                # guard (PySide inspects imported Python modules on disk).
                self.assertEqual(original.tobytes(),rgba(self.icon_image(model)).tobytes())
        self.assertEqual(before,self.snapshot())

    def test_pending_old_mode_is_rejected_even_after_off_on_off(self):
        model=self.make_model()
        key=model.key(self.row)
        with patch.object(model.pool,'submit') as submit:
            model.data(model.index(0),Qt.ItemDataRole.DecorationRole)
            old_request=(key,0)
            self.assertIn(old_request,model.pending)
            model.set_brightness_strength(50)
            model.set_brightness_strength(0)
            model.loaded(old_request,auto_brightness(QImage.fromData(self.blob)))
            self.assertNotIn(key,model.cache)
            self.assertNotIn(old_request,model.pending)
            model.data(model.index(0),Qt.ItemDataRole.DecorationRole)
            self.assertEqual(submit.call_args.args[1],(key,2))
        model.loaded((key,2),QImage.fromData(self.blob))
        self.assertEqual(rgba(self.icon_image(model))[50,50,1],20)

    def test_legacy_checkbox_and_explicit_zero_settings(self):
        self.assertEqual(saved_strength(self.db),0)
        self.db.set_setting('display_auto_brightness','1')
        self.assertEqual(saved_strength(self.db),50)
        self.db.set_setting('display_brightness_strength','0')
        self.assertEqual(saved_strength(self.db),0)
        self.db.set_setting('display_brightness_strength','73')
        self.assertEqual(saved_strength(self.db),73)
        self.db.set_setting('display_brightness_strength','invalid')
        self.assertEqual(saved_strength(self.db),0)

    def test_obsolete_queued_slider_work_does_not_read_database(self):
        model=self.make_model()
        key=model.key(self.row)
        model.set_brightness_strength(75)
        with patch.object(self.db,'thumbnail',side_effect=AssertionError('Obsolete request')):
            model.load((key,0),50)
        self.assertNotIn(key,model.cache)

    def test_multipage_tiles_adjust_each_page_before_drawing(self):
        model=self.make_model()
        view=HoverListView();view.setModel(model);self.views.append(view)
        key=model.key(self.row)
        view.tiles_signals.ready.disconnect(view.tiles_ready)
        results=[]
        view.tiles_signals.ready.connect(lambda request,canvas:results.append((request,canvas)))
        for strength in [0,50]:
            model.set_brightness_strength(strength)
            view.hover_key=key
            view.build_tiles((key,600,400,False,strength,model.display_generation),self.row)
        self.assertEqual(len(results),2)
        off=rgba(results[0][1]);on=rgba(results[1][1])
        green=(off[:,:,1]>10)&(off[:,:,1]<30)&(off[:,:,0]<10)
        self.assertGreater(int(green.sum()),500)
        self.assertGreater(float(on[:,:,1][green].mean()),35)
        # White labels/background remain unchanged; enhancement is per image.
        np.testing.assert_array_equal(on[off[:,:,1]==255],off[off[:,:,1]==255])
        view.hover_key=key
        with patch.object(view,'show_hover') as show:
            view.tiles_ready(results[0][0],results[0][1])
            show.assert_not_called()

    def test_single_hover_uses_adjusted_icon_once(self):
        model=self.make_model(page=True)
        view=HoverListView();self.views.append(view)
        view.setModel(model);view.setViewMode(view.ViewMode.IconMode)
        view.setIconSize(QSize(180,140));view.setGridSize(QSize(210,188))
        view.resize(640,400);view.show();self.app.processEvents()
        view.set_brightness_strength(50)
        expected=self.icon_image(model)
        point=view.visualRect(model.index(0)).center()
        QCursor.setPos(view.viewport().mapToGlobal(point))
        view.hover_key=model.key(model.items[0]);view.show_scale_bar=False
        with patch.object(view,'display_popup') as display:
            view.show_hover()
            display.assert_called_once()
            np.testing.assert_array_equal(rgba(display.call_args.args[0].toImage()),rgba(expected))

    def test_main_slider_persistence_language_and_child_window_sync(self):
        before=self.snapshot()
        with patch.object(Service,'start'),patch('feature_indexing.FeatureIndexPanel.start'):
            window=Window(self.db)
            window.show()
            try:
                self.wait(lambda:len(window.model.items)==1 and window.list_future is None)
                dialog=PagesWindow(self.db,self.row,window);dialog.show()
                try:
                    self.assertEqual(window.brightness_slider.value(),0)
                    self.assertFalse(hasattr(window,'auto_brightness'))
                    self.assertEqual(window.brightness_label.text(),'輝度補正')
                    self.assertEqual(window.brightness_value.text(),'0（オフ）')
                    window.brightness_slider.setValue(50)
                    window.brightness_slider.sliderReleased.emit()
                    self.assertEqual(window.model.brightness_strength,50)
                    self.assertEqual(dialog.model.brightness_strength,50)
                    self.assertEqual(self.db.get_setting('display_brightness_strength',''),'50')
                    self.assertGreater(rgba(self.icon_image(window.model))[50,50,1],35)
                    self.assertEqual(before,self.snapshot())
                    with patch.object(window.view,'set_brightness_strength',wraps=window.view.set_brightness_strength) as apply:
                        for value in [10,20,30,50]:
                            window.brightness_slider.setValue(value)
                        apply.assert_not_called()
                        window.brightness_slider.setValue(0)
                        window.brightness_slider.sliderReleased.emit()
                        apply.assert_called_once_with(0)
                    self.assertEqual(window.brightness_value.text(),'0（オフ）')
                    self.assertEqual(rgba(self.icon_image(window.model))[50,50,1],20)
                    window.brightness_slider.setValue(50)
                    self.wait(lambda:window.model.brightness_strength==50)
                    window.language_combo.setCurrentIndex(1)
                    self.assertEqual(window.brightness_label.text(),'Brightness')
                    self.assertIn('Display only',window.brightness_slider.toolTip())
                    self.assertEqual(window.brightness_slider.value(),50)
                    window.brightness_slider.setValue(73)  # Close before timer fires.
                finally:
                    dialog.close();self.app.processEvents()
            finally:
                window.quit_app()
                self.wait(lambda:window.model.pool._shutdown)
            restored=Window(self.db)
            try:
                self.assertEqual(restored.brightness_slider.value(),73)
                self.assertEqual(restored.model.brightness_strength,73)
            finally:
                restored.quit_app()
                self.wait(lambda:restored.model.pool._shutdown)


if __name__=='__main__':
    unittest.main()
