"""Integrated catalog results, live deletion, filters and worker shutdown."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from app import Window
from catalog import Catalog
from test_visual_search import picture,png
from test_feature_indexing import FakeDino


class CatalogSearchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self,predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.app.processEvents()
            if predicate(): return
            time.sleep(.01)
        self.fail('GUI deadline')

    def settled(self):
        return self.window.list_future is None and not self.window.refresh_timer.isActive()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)/'cloud';root.mkdir()
        self.db=Catalog(Path(self.tmp.name)/'local'/'db.sqlite3')
        self.db.set_setting('scan_enabled','0');self.db.set_setting('generate','0')
        self.db.set_setting('image_search_detailed','1')  # Old slow default must not carry over.
        self.db.set_setting('image_search_engine','standard')  # Old preference is ignored.
        self.db.add_root(root)
        for i in range(4): (root/f'sample{i}.tif').write_bytes(b'placeholder')
        self.db.scan(self.db.roots()[0])
        for i,row in enumerate(self.db.rows()):
            self.db.save_thumb(row,png(picture(i)),[png(picture(i)),png(picture(i+10))],page_count=2)
        for p in root.iterdir(): p.unlink()  # All interactions below must use cached data.
        auto=patch('feature_indexing.FeatureIndexPanel.start');auto.start();self.addCleanup(auto.stop)
        # UI lifecycle tests use a cheap stand-in at the DINO boundary. Real
        # model compatibility is covered separately by the offline DINO tests.
        for mock in (patch('download_search_model.check_ai_packages'),
                     patch('download_search_model.ensure_model'),
                     patch('visual_search.DinoEncoder',FakeDino)):
            mock.start();self.addCleanup(mock.stop)
        self.window=Window(self.db);self.window.show()
        self.window.feature_index.set_status('特徴抽出は待機中です。',{})
        self.addCleanup(self.stop)
        self.wait(lambda:len(self.window.model.items)==4 and self.settled())

    def stop(self):
        self.window.quit_app()
        self.wait(lambda:self.window.exiting and not self.window.service.isRunning() and
                  (self.window.image_search_panel is None or self.window.image_search_panel.worker is None))
        self.app.processEvents()

    def search(self):
        self.window.image_captured(QImage.fromData(png(picture(12).crop((70,60,420,320)))))
        self.wait(lambda:self.window.image_search_panel.worker is None and
                  self.window.image_search_visible and self.settled())

    def test_panel_opens_without_capture_or_search_and_selection_is_explicit(self):
        w=self.window
        self.assertIsNone(w.image_search_panel)
        self.assertTrue(w.feature_index.isHidden())
        from screen_capture import ScreenCapture
        with patch.object(ScreenCapture,'start') as capture, \
             patch('image_search_dialog.SearchWorker.start') as search:
            w.image_search_button.click()
            panel=w.image_search_panel
            self.assertTrue(panel.isVisible())
            self.assertTrue(w.image_search_button.isChecked())
            self.assertIsNone(w.screen_capture)
            self.assertFalse(panel.search_button.isEnabled())
            self.assertFalse(panel.progress.isVisible())
            self.assertFalse(hasattr(panel,'mode'))
            self.assertEqual(panel.capture_button.text(),'範囲選択')
            capture.assert_not_called();search.assert_not_called()
            w.image_search_button.click()
            self.assertTrue(panel.isHidden())
            w.image_search_button.click()
            self.assertIs(w.image_search_panel,panel)
            capture.assert_not_called()
            panel.capture_button.click()
            capture.assert_called_once()
            self.assertIsNotNone(w.screen_capture)
            self.assertFalse(w.image_search_button.isEnabled())
            # Cancelling an actual controller preserves the expanded panel.
            w.screen_capture.finish()
            self.assertIsNone(w.screen_capture)
            self.assertTrue(panel.isVisible())
            self.assertTrue(w.image_search_button.isEnabled())
            self.assertFalse(panel.query)
            search.assert_not_called()
            w.image_search_button.click()
            self.assertTrue(panel.isHidden())
            self.assertFalse(w.image_search_button.isChecked())

    def test_panel_hide_reopen_preserves_query_results_and_dino_only_features(self):
        folder=str(Path(self.tmp.name)/'custom model location')
        self.db.set_setting('image_search_model',folder)
        with patch('download_search_model.ensure_model') as ensure:
            self.search()
            self.assertEqual(ensure.call_args.args[0],folder)
        self.assertEqual(self.db.get_setting('image_search_model',''),folder)
        w=self.window;panel=w.image_search_panel
        query,hits=panel.query,w.image_search_hits
        self.assertEqual(self.db.get_setting('image_search_engine',''),'dino')
        with self.db.connect() as c:
            engines={row[0] for row in c.execute('SELECT DISTINCT engine FROM visual_features')}
        self.assertEqual(engines,{FakeDino.key})
        with patch.object(panel,'start_search',side_effect=AssertionError('Unrequested search')):
            w.image_search_button.click()
            self.assertTrue(panel.isHidden())
            self.assertTrue(w.image_search_visible)
            self.assertEqual(w.image_search_hits,hits)
            w.image_search_button.click()
            self.assertTrue(panel.isVisible())
            self.assertEqual(panel.query,query)
            self.assertIs(w.image_search_panel,panel)
            self.assertFalse(panel.progress.isVisible())
            w.language_combo.setCurrentIndex(1)
            self.assertEqual(panel.capture_button.text(),'Select region')

    def test_catalog_results_navigation_and_original_filter_restore(self):
        w=self.window
        w.search.setText('sample')
        w.sort_order.setCurrentIndex(w.sort_order.findData('newest'))
        self.wait(self.settled)
        model=w.model
        self.search()
        panel=w.image_search_panel
        self.assertFalse(panel.isWindow())
        self.assertIs(w.view.model(),model)
        self.assertFalse(panel.detailed.isChecked())
        self.assertEqual(w.model.items[0]['name'],'sample2.tif')
        hit=w.model.items[0]
        self.assertEqual(hit['search_page'],2)
        self.assertEqual(len(w.model.items),4)
        self.assertIn('一致ページ: 2',w.details.info.toPlainText())
        w.model.data(w.model.index(0),Qt.ItemDataRole.DecorationRole)
        self.wait(lambda:w.model.key(hit) in w.model.cache)
        pix=w.model.cache[w.model.key(hit)].pixmap(QSize(512,512),1.0)
        expected=QImage.fromData(self.db.page_thumbnail(hit['id'],hit['registration_uid'],hit['revision'],2))
        self.assertEqual(pix.toImage().pixelColor(200,200),expected.pixelColor(200,200))
        self.assertTrue(hit['search_polygon'])
        w.view.doItemsLayout()
        pos=w.view.visualRect(w.model.index(0)).center()
        # Native cursor dispatch varies with offscreen Qt and other test windows.
        # Exercise the actual hover renderer with a deterministic pointer position;
        # smoke_pages separately covers the mouse-event/timer path.
        w.view.hover_key=w.model.key(hit)
        with patch('app.QCursor.pos',return_value=w.view.viewport().mapToGlobal(pos)):
            w.view.show_hover()
        self.assertTrue(w.view.popup.isVisible())
        preview=w.view.popup.pixmap().toImage()
        cyan=QColor('#00e5ff')
        self.assertTrue(any(preview.pixelColor(x,y)==cyan for y in range(preview.height()) for x in range(preview.width())))
        w.view.clear_hover()
        w.view.setCurrentIndex(w.model.index(1))
        second=w.details.uid
        w.details.memo.setPlainText('memo in search result')
        self.assertTrue(w.details.save())
        self.assertEqual(self.db.file_record(second)['memo'],'memo in search result')
        self.wait(self.settled)
        # Going back and returning must not invoke a search or lose the candidate.
        with patch.object(panel,'start_search',side_effect=AssertionError('Unrequested repeat search')):
            panel.toggle_button.click()
            self.wait(lambda:not w.image_search_visible and self.settled())
            self.assertEqual(w.search.text(),'sample')
            self.assertEqual(w.sort_order.currentData(),'newest')
            panel.toggle_button.click()
            self.wait(lambda:w.image_search_visible and self.settled())
            self.assertEqual(w.details.uid,second)
            w.view.setCurrentIndex(w.model.index(2))
            self.assertIsNone(panel.worker)
        w.language_combo.setCurrentIndex(1)
        self.wait(self.settled)
        self.assertEqual(panel.toggle_button.text(),'Back to catalog')
        self.assertIn('Image search:',panel.status.text())
        self.assertIn('Matching page:',w.details.info.toPlainText())
        if os.environ.get('MEDIA_CATALOG_QA_SCREENSHOT'):
            for i in range(w.model.rowCount()):
                w.model.data(w.model.index(i),Qt.ItemDataRole.DecorationRole)
            self.wait(lambda:all(w.model.key(row) in w.model.cache for row in w.model.items))
            self.app.processEvents()
            w.grab().save(os.environ['MEDIA_CATALOG_QA_SCREENSHOT'])

    def test_display_revalidates_deleted_missing_and_regenerated_hits(self):
        self.search()
        w=self.window
        rows=list(w.model.items)
        with self.db.connect() as c:
            c.execute('DELETE FROM files WHERE id=?',(rows[0]['id'],))
            c.execute('UPDATE files SET missing=1 WHERE id=?',(rows[1]['id'],))
        self.db.save_thumb(rows[2],png(picture(30)))
        w.refresh()
        self.wait(self.settled)
        self.assertEqual([r['registration_uid'] for r in w.model.items],[rows[3]['registration_uid']])
        w.image_search_panel.toggle_button.click();self.wait(self.settled)
        w.image_search_panel.toggle_button.click();self.wait(self.settled)
        self.assertEqual(len(w.model.items),1)

    def test_scope_toggle_and_text_filter_use_existing_catalog(self):
        self.search()
        w=self.window
        w.search.setText('sample2')
        self.wait(lambda:len(w.model.items)==1 and self.settled())
        panel=w.image_search_panel
        panel.scope.setChecked(True)
        panel.search_button.click()
        self.wait(lambda:panel.worker is None and self.settled())
        self.assertEqual(len(w.image_search_hits),1)
        self.assertEqual(w.model.items[0]['name'],'sample2.tif')

    def test_cancel_preserves_results_and_shutdown_waits_for_worker(self):
        self.search()
        w=self.window;panel=w.image_search_panel
        previous=w.image_search_hits
        from visual_search import SearchCancelled
        def until_cancel(index,query,encoder,stop,*args,**kwargs):
            while not stop(): time.sleep(.005)
            raise SearchCancelled()
        with patch('visual_search.SearchIndex.search',until_cancel):
            panel.start_search();self.assertIsNotNone(panel.worker)
            w.image_search_button.click()
            self.assertTrue(panel.isHidden())
            self.assertFalse(panel.worker.stop_event.is_set())
            w.image_search_button.click()
            self.assertTrue(panel.isVisible())
            panel.cancel_search()
            self.wait(lambda:panel.worker is None)
            self.assertEqual(w.image_search_hits,previous)
            self.assertEqual(len(w.model.items),4)
            panel.start_search();self.assertIsNotNone(panel.worker)
            w.quit_app()
            self.wait(lambda:w.exiting and panel.worker is None and not w.service.isRunning())


if __name__=='__main__': unittest.main()
