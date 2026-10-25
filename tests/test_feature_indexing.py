"""Consent boundaries, generated-thumbnail events and DINO-only cache lifecycle."""
import builtins
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QMessageBox
from catalog import Catalog
from app import Service
from feature_indexing import FeatureWorker, FeatureIndexPanel, DINO_PENDING
from visual_search import StandardEncoder, SearchIndex, SearchCancelled, index_lock, dino_model_key
from download_search_model import ensure_model
from test_visual_search import picture, png
from test_model_download import tiny_model


class FakeDino(StandardEncoder):
    key = 'visual-v1:dinov2:test'

    def __init__(self, folder, stop):
        super().__init__()


class DownloadedDino(StandardEncoder):
    def __init__(self, folder, stop):
        super().__init__()
        self.key = dino_model_key(folder, stop)


SPECS = {'DINO': (FakeDino.key, 'local-model')}


class AutomaticFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'cloud'
        self.root.mkdir()
        self.db = Catalog(Path(self.tmp.name)/'local'/'catalog.sqlite3')
        self.db.add_root(self.root)
        self.db.set_setting('image_search_engine', 'standard')
        self.db.set_setting('image_search_model', str(Path(self.tmp.name)/'missing-model'))
        self.index = SearchIndex(self.db)
        self.panels = []
        self.addCleanup(self.stop_panels)
        # Fake encoders use no model files. Real-model tests still exercise the
        # actual readiness/download entry point (with networking forbidden).
        mock = patch('feature_indexing.ensure_model', side_effect=lambda folder, *args:
                     Path(folder) if folder == 'local-model' else ensure_model(folder, *args))
        mock.start()
        self.addCleanup(mock.stop)

    def stop_panels(self):
        for panel in self.panels:
            panel.shutdown()
            self.assertTrue(panel.worker.wait(10000), 'feature worker did not stop')
            panel.close()
        self.app.processEvents()

    def add(self, name, pages=1):
        source = self.root/name
        source.write_bytes(png(picture(len(list(self.root.iterdir())))))
        self.db.scan(self.db.roots()[0])
        row = next(r for r in self.db.rows() if r['name'] == name)
        if pages:
            data = png(picture(row['id']))
            self.db.save_thumb(row, data, [data]*pages, page_count=pages)
        return self.db.file_record(row['registration_uid'])

    def wait_until(self, predicate, seconds=12):
        deadline = time.monotonic()+seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertTrue(predicate(), 'event-loop deadline')

    def panel(self):
        panel = FeatureIndexPanel(self.db)
        self.panels.append(panel)
        panel.show()
        return panel

    def count(self, engine, uid=None):
        with self.db.connect() as c:
            return c.execute('SELECT count(*) FROM visual_features WHERE engine=?'+
                (' AND registration_uid=?' if uid else ''), (engine, uid) if uid else (engine,)).fetchone()[0]

    def test_visibility_hides_idle_completion_and_cancel_but_shows_work_and_errors(self):
        panel = self.panel()
        panel.set_status('特徴抽出は待機中です。', {})
        self.assertTrue(panel.isHidden())
        panel.set_busy(True)
        panel.set_status('特徴抽出中: {engine} / 全体 {done} / {total}ファイル',
                         dict(engine='DINO', done=1, total=65))
        self.assertTrue(panel.isVisible())
        self.assertTrue(panel.cancel_button.isVisible())
        panel.set_busy(False)
        panel.set_status('特徴抽出完了: 全体 {done} / {total}ファイル', dict(done=65,total=65))
        self.assertTrue(panel.isHidden())
        panel.retranslate()
        self.assertTrue(panel.isHidden())
        panel.set_busy(True)
        self.assertTrue(panel.isVisible())
        panel.set_status('特徴抽出を中止しました。完了済みデータは保持します。', {})
        panel.set_busy(False)
        self.assertTrue(panel.isHidden())
        panel.set_availability({'DINO':'offline'})
        self.assertTrue(panel.isVisible())
        self.assertTrue(panel.notice.isVisible())
        self.assertFalse(panel.label.isVisible())
        panel.set_availability({})
        self.assertTrue(panel.isHidden())

    def test_existing_dino_reused_without_standard_prompt_or_rewriting_vectors(self):
        row = self.add('cached.tif', 2)
        self.index.build(FakeDino('local-model', lambda:False))
        with self.db.connect() as c:
            before = list(c.execute('SELECT engine,vectors,local_data FROM visual_features ORDER BY page_number'))
        probe = FeatureWorker(self.db)
        with patch.object(probe,'available',return_value=(SPECS,{})), \
             patch('feature_indexing.DinoEncoder',side_effect=AssertionError('reloaded complete features')):
            report = probe.inspect_startup()
            self.assertEqual(report['counts'],{'DINO':0})
            self.assertFalse(report['uids'])
            self.assertEqual(probe.process_batch([row['registration_uid']]),0)
        with self.db.connect() as c:
            after = list(c.execute('SELECT engine,vectors,local_data FROM visual_features ORDER BY page_number'))
        self.assertEqual([tuple(r) for r in before],[tuple(r) for r in after])
        self.assertEqual(self.count(StandardEncoder.key),0)

    def test_startup_confirmation_yes_extracts_only_dino_without_source_reads(self):
        self.add('old.tif', 2)
        for p in self.root.iterdir(): p.unlink()
        panel = self.panel()
        real_open = builtins.open
        def guard(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and str(path).startswith(str(self.root)):
                raise AssertionError('Original file was read')
            return real_open(path, *args, **kwargs)
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', FakeDino), patch('builtins.open', side_effect=guard):
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(FakeDino.key), 0)
            self.assertEqual(panel.worker.encoders, {}, 'loaded an encoder before consent')
            self.assertIn('2', panel.prompt.text())
            panel.prompt.done(QMessageBox.StandardButton.Yes)
            self.wait_until(lambda: self.count(FakeDino.key) == 2)
            self.wait_until(panel.isHidden)
            self.assertEqual(self.count(StandardEncoder.key), 0)
            report = panel.worker.inspect_startup()
            self.assertEqual(report['counts'], {'DINO': 0})
            panel.confirm_startup(report)
            self.assertIsNone(panel.prompt)

    def test_declining_backlog_only_newly_saved_thumbnail_is_indexed(self):
        old = self.add('old.png')
        new = self.add('new.png', 0)
        panel = self.panel()
        service = Service(self.db)
        service.thumbnail_saved.connect(panel.thumbnail_saved)
        emitted = []
        service.thumbnail_saved.connect(emitted.append)
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', FakeDino):
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            panel.prompt.done(QMessageBox.StandardButton.No)
            service.make_thumbnail(new)
            self.wait_until(lambda: self.count(FakeDino.key, new['registration_uid']) == 1)
            self.assertEqual(emitted, [new['registration_uid']])
            self.assertEqual(self.count(StandardEncoder.key, new['registration_uid']), 0)
            self.assertEqual(self.count(StandardEncoder.key, old['registration_uid']), 0)
            self.assertEqual(self.count(FakeDino.key, old['registration_uid']), 0)
            # Stale jobs must not emit a generation event, even if the source stat matches.
            service.make_thumbnail(dict(new, registration_uid='retired-registration'))
            self.app.processEvents()
            self.assertEqual(len(emitted), 1)
        # Declining is not a permanent opt-out: remaining pages are offered next launch.
        probe = FeatureWorker(self.db)
        with patch.object(probe, 'available', return_value=(SPECS, {})):
            self.assertEqual(probe.inspect_startup()['uids'], [old['registration_uid']])

    def test_dino_unavailable_does_not_fall_back_to_standard(self):
        row = self.add('one.png')
        panel = self.panel()
        with patch('feature_indexing.check_ai_packages', side_effect=RuntimeError('AI packages missing')):
            panel.start()
            self.wait_until(lambda: panel.state[0] == '特徴抽出は待機中です。')
            self.assertIsNone(panel.prompt)
            self.assertIn('DINO', panel.unavailable)
            self.assertTrue(panel.notice.isVisible())
            self.assertFalse(panel.label.isVisible())
            panel.thumbnail_saved(row['registration_uid'])
            self.wait_until(lambda: '一部保留' in panel.state[0])
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(FakeDino.key), 0)
            self.assertEqual(panel.worker.inspect_startup()['counts'], {})

    def test_cancel_preserves_completed_pages_and_restart_only_offers_missing(self):
        row = self.add('many.tif', 3)
        panel = self.panel()
        gate, entered = threading.Event(), threading.Event()
        calls = []
        original = StandardEncoder.encode
        def slow(encoder, image, multiple=True):
            calls.append(1)
            if len(calls) == 2:
                entered.set()
                if not gate.wait(8): raise AssertionError('test gate timed out')
            return original(encoder, image, multiple)
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', FakeDino), patch.object(FakeDino, 'encode', slow):
            try:
                panel.start()
                self.wait_until(lambda: panel.prompt is not None)
                panel.prompt.done(QMessageBox.StandardButton.Yes)
                self.wait_until(entered.is_set)
                panel.worker.cancel()
            finally:
                gate.set()
            self.wait_until(lambda: '中止しました' in panel.label.text())
            self.assertEqual(self.count(FakeDino.key), 1)
            probe = FeatureWorker(self.db)
            with patch.object(probe, 'available', return_value=(SPECS, {})):
                report = probe.inspect_startup()
            self.assertEqual(report['counts'], {'DINO': 2})
            panel.worker.enqueue(report['uids'])
            self.wait_until(lambda: self.count(FakeDino.key) == 3)
            self.assertEqual(len(calls), 4, 'recomputed an already completed page')

    def test_dino_only_backlog_and_thumbnail_revision(self):
        row = self.add('one.tif', 2)
        self.index.build(StandardEncoder())
        with self.db.connect() as c:
            legacy = [tuple(r) for r in c.execute('SELECT vectors,local_data FROM visual_features WHERE engine=? ORDER BY page_number', (StandardEncoder.key,))]
        probe = FeatureWorker(self.db)
        with patch.object(probe, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', FakeDino):
            self.assertEqual(probe.inspect_startup()['counts'], {'DINO': 2})
            probe.process_batch([row['registration_uid']])
            self.assertEqual(self.count(FakeDino.key), 2)
            with self.db.connect() as c:
                self.assertEqual(legacy,[tuple(r) for r in c.execute('SELECT vectors,local_data FROM visual_features WHERE engine=? ORDER BY page_number', (StandardEncoder.key,))])
            data = png(picture(65))
            self.db.save_thumb(row, data, [data], page_count=1)
            self.assertEqual(probe.inspect_startup()['counts'], {'DINO': 1})
            probe.process_batch([row['registration_uid']])
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(FakeDino.key), 1)

    def test_broken_dino_does_not_poison_cache_or_fall_back_to_standard(self):
        row = self.add('one.png')
        probe = FeatureWorker(self.db)
        with patch.object(probe, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', side_effect=RuntimeError('model load failed')):
            probe.process_batch([row['registration_uid']])
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(FakeDino.key), 0)
            self.assertEqual(probe.inspect_startup()['counts']['DINO'], 1)

    def test_legacy_and_corrupt_previews_do_not_repeat_prompt(self):
        row = self.add('old.png')
        with self.db.connect() as c:
            c.execute('DELETE FROM page_thumbs')
            c.execute('UPDATE thumbs SET data=?', (b'broken thumbnail',))
        self.assertEqual(self.index.pending(StandardEncoder.key), {row['registration_uid']: 1})
        self.index.build(StandardEncoder(), uid=row['registration_uid'])
        self.assertFalse(self.index.pending(StandardEncoder.key))
        with self.db.connect() as c:
            self.assertTrue(c.execute('SELECT error FROM visual_features').fetchone()[0])

    def test_startup_counts_do_not_read_thumbnail_blobs(self):
        row = self.add('pages.tif', 3)
        connect = self.db.connect
        def authorize(action, table, column, *rest):
            if action == sqlite3.SQLITE_READ and table in ('thumbs', 'page_thumbs') and column == 'data':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        @contextmanager
        def metadata_only():
            with connect() as c:
                c.set_authorizer(authorize)
                yield c
        with patch.object(self.db, 'connect', metadata_only):
            self.assertEqual(self.index.pending(StandardEncoder.key), {row['registration_uid']: 3})

    def test_shutdown_dismisses_unanswered_confirmation(self):
        self.add('one.png')
        panel = self.panel()
        panel.start()
        self.wait_until(lambda: panel.prompt is not None)
        panel.shutdown()
        self.assertTrue(panel.worker.wait(5000))
        self.assertIsNone(panel.prompt)
        self.assertEqual(self.count(StandardEncoder.key), 0)

    def test_missing_model_prompt_decline_does_not_download(self):
        self.add('old.png')
        self.index.build(StandardEncoder())
        panel = self.panel()
        with patch('feature_indexing.check_ai_packages'), \
             patch('feature_indexing.importlib.util.find_spec', return_value=object()), \
             patch('feature_indexing.ensure_model') as download:
            report = panel.worker.inspect_startup()
            self.assertEqual(report['counts'], {'DINO': 1})
            self.assertTrue(report['download'])
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            self.assertIn('88 MB', panel.prompt.informativeText())
            panel.prompt.done(QMessageBox.StandardButton.No)
            download.assert_not_called()
            self.assertFalse(panel.worker.encoders)

    def test_approved_or_generated_jobs_download_then_index_and_reuse(self):
        row = self.add('old.png')
        folder = self.db.get_setting('image_search_model', '')
        probe = FeatureWorker(self.db)
        def obtain(path, *args):
            tiny_model(path)
        with patch('feature_indexing.check_ai_packages'), \
             patch('feature_indexing.importlib.util.find_spec', return_value=object()), \
             patch('feature_indexing.ensure_model', side_effect=obtain) as download, \
             patch('feature_indexing.DinoEncoder', DownloadedDino):
            probe.process_batch([row['registration_uid']])
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(dino_model_key(folder)), 1)
            self.assertEqual(self.count(DINO_PENDING), 0)
            self.assertEqual(download.call_count, 1)
            probe.process_batch([row['registration_uid']])
            self.assertEqual(download.call_count, 1)
            self.assertEqual(probe.inspect_startup()['counts'], {'DINO': 0})

    def test_download_failure_does_not_fall_back_or_retry_every_batch(self):
        row = self.add('old.png')
        probe = FeatureWorker(self.db)
        with patch('feature_indexing.check_ai_packages'), \
             patch('feature_indexing.importlib.util.find_spec', return_value=object()), \
             patch('feature_indexing.ensure_model', side_effect=OSError('offline')) as download:
            probe.process_batch([row['registration_uid']])
            self.assertEqual(self.count(StandardEncoder.key), 0)
            self.assertEqual(self.count(DINO_PENDING), 0)
            probe.process_batch([row['registration_uid']])
            self.assertEqual(download.call_count, 1)
            self.assertIn('offline', probe.available()[1]['DINO'])
            # A later manual download or AI search can restore availability.
            tiny_model(self.db.get_setting('image_search_model', ''))
            self.assertIn('DINO', probe.available()[0])

    def test_explicit_ai_search_downloads_missing_model(self):
        from image_search_dialog import SearchWorker
        row = self.add('old.png')
        folder = self.db.get_setting('image_search_model', '')
        worker = SearchWorker(self.db, png(picture(row['id'])), folder, None, True, False)
        results, errors = [], []
        worker.completed.connect(results.append)
        worker.failed.connect(errors.append)
        with patch('download_search_model.check_ai_packages'), \
             patch('download_search_model.ensure_model', side_effect=lambda path, *a: tiny_model(path)) as download, \
             patch('visual_search.DinoEncoder', DownloadedDino):
            worker.run()
            self.assertEqual(download.call_count, 1)
        self.assertFalse(errors)
        self.assertEqual(results[0].hits[0]['registration_uid'], row['registration_uid'])

    def test_empty_catalog_does_not_trigger_download(self):
        from image_search_dialog import SearchWorker
        worker = SearchWorker(self.db, png(picture(2)),
                              self.db.get_setting('image_search_model', ''), None, True, False)
        errors = []
        worker.failed.connect(errors.append)
        with patch('download_search_model.check_ai_packages'), \
             patch('download_search_model.ensure_model') as download:
            worker.run()
            download.assert_not_called()
        self.assertTrue(errors)
        probe = FeatureWorker(self.db)
        with patch('feature_indexing.check_ai_packages'), \
             patch('feature_indexing.importlib.util.find_spec', return_value=object()):
            self.assertFalse(probe.inspect_startup()['download'])

    def test_search_missing_ai_packages_reports_error_without_standard_fallback(self):
        from image_search_dialog import SearchWorker
        row = self.add('one.png')
        self.index.build(StandardEncoder())
        worker = SearchWorker(self.db, png(picture(row['id'])), 'local-model', None, False, False)
        results, errors = [], []
        worker.completed.connect(results.append)
        worker.failed.connect(errors.append)
        with patch('download_search_model.check_ai_packages', side_effect=RuntimeError('AI packages missing')), \
             patch('download_search_model.ensure_model') as download, \
             patch('visual_search.StandardEncoder', side_effect=AssertionError('Standard fallback')):
            worker.run()
            download.assert_not_called()
        self.assertFalse(results)
        self.assertEqual(errors,['AI packages missing'])
        self.assertEqual(self.count(StandardEncoder.key),1)

    def test_waiting_for_search_cache_lock_is_cancellable(self):
        row = self.add('one.png')
        panel = self.panel()
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), patch('feature_indexing.DinoEncoder', FakeDino):
            with index_lock(lambda: False):
                panel.start()
                self.wait_until(lambda: panel.prompt is not None)
                panel.prompt.done(QMessageBox.StandardButton.Yes)
                self.wait_until(lambda: panel.cancel_button.isEnabled())
                panel.shutdown()
                self.assertTrue(panel.worker.wait(5000), 'shutdown blocked on cache lock')
            self.assertEqual(self.count(StandardEncoder.key), 0)

    @unittest.skipUnless(os.environ.get('MEDIA_CATALOG_TEST_DINO'), 'optional local DINOv2 model')
    def test_real_dino_automatic_offline(self):
        import socket
        row = self.add('real.png')
        self.db.set_setting('image_search_model', os.environ['MEDIA_CATALOG_TEST_DINO'])
        panel = self.panel()
        with patch.object(socket, 'socket', side_effect=AssertionError('Network access during automatic extraction')):
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            self.assertFalse(panel.unavailable)
            self.assertEqual(panel.worker.encoders, {})
            for p in self.root.iterdir(): p.unlink()
            panel.prompt.done(QMessageBox.StandardButton.Yes)
            self.wait_until(lambda: self.count(panel.worker.model_key) == 1, seconds=45)
            self.assertEqual(self.count(StandardEncoder.key), 0)


if __name__ == '__main__':
    unittest.main()
