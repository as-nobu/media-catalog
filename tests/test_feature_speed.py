"""Legacy cache coexistence, bounded commits, and whole-job progress."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_search as vs
from catalog import Catalog
from feature_indexing import FeatureWorker
import test_visual_search as search_tests
import test_feature_indexing as indexing_tests
from test_visual_search import picture, png
from test_feature_indexing import FakeDino, SPECS


class CacheCompatibilityTests(unittest.TestCase):
    setUp = search_tests.SearchTests.setUp
    add = search_tests.SearchTests.add

    def legacy(self, row, page, encoder):
        with self.db.connect() as c:
            image = vs.decode(c.execute('SELECT data FROM page_thumbs WHERE file_id=? AND page_number=?',
                                        (row['id'], page)).fetchone()[0])
            c.execute('INSERT INTO visual_features VALUES (?,?,?,?,?,?,?,?)',
                      (row['id'], page, encoder.key, row['registration_uid'], row['revision'],
                       vs.pack(vectors=encoder.encode(image)), vs.pack(**vs.local_features(image)), ''))

    def snapshot(self):
        with self.db.connect() as c:
            return {tuple(r[:3]): tuple(r) for r in c.execute('SELECT * FROM visual_features')}

    def test_partial_legacy_cache_kept_byte_for_byte_and_mixed_search(self):
        first = self.add('first.tif', [picture(1), picture(2)])
        second = self.add('second.tif', [picture(3), picture(4)])
        standard, dino = vs.StandardEncoder(), FakeDino('', lambda: False)
        for page in (1, 2): self.legacy(first, page, standard)
        self.legacy(first, 1, dino)
        self.legacy(second, 1, standard)
        before = self.snapshot()
        records = self.db.rows()
        # Upgrade an old DB with no shared table, while its cache is incomplete.
        with self.db.connect() as c: c.execute('DROP TABLE visual_local_features')
        self.db = Catalog(self.db.path)
        self.index = vs.SearchIndex(self.db)
        self.assertEqual(records, self.db.rows())
        self.assertIsNone(self.db.next_job())
        for path in self.root.iterdir(): path.unlink()
        with patch('visual_search.local_features', wraps=vs.local_features) as sift:
            self.assertEqual(self.index.build(standard), 1)
            self.assertEqual(self.index.build(dino), 3)
            self.assertEqual(sift.call_count, 1, 'SIFT should run only for the never-indexed page')
        after = self.snapshot()
        for key, value in before.items(): self.assertEqual(value, after[key])
        self.assertFalse(self.index.pending(standard.key))
        self.assertFalse(self.index.pending(dino.key))
        for encoder in (standard, dino):
            for seed, row, page in ((1, first, 1), (2, first, 2), (4, second, 2)):
                report = self.index.search(png(picture(seed)), encoder)
                self.assertEqual(report.indexed, 0)
                self.assertEqual(report.hits[0]['registration_uid'], row['registration_uid'])
                self.assertEqual(report.hits[0]['page_number'], page)
                self.assertGreaterEqual(report.hits[0]['inliers'], 8)

    def test_completed_legacy_rows_need_no_blob_reads_or_extraction(self):
        row = self.add('old.png', [picture(8)])
        encoder = vs.StandardEncoder()
        self.legacy(row, 1, encoder)
        original = self.snapshot()
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
        with patch.object(self.db, 'connect', metadata_only), patch.object(encoder, 'encode', side_effect=AssertionError('Re-extraction')):
            self.assertFalse(self.index.pending(encoder.key))
            self.assertEqual(self.index.thumbnail_uids(), {row['registration_uid']})
            # The fully completed build must stop before preparing a blob query.
            self.assertEqual(self.index.build(encoder), 0)
        self.assertEqual(original, self.snapshot())

    def test_new_pages_share_sift_and_backup_and_invalidate_together(self):
        row = self.add('new.tif', [picture(5), picture(6)])
        with patch('visual_search.local_features', wraps=vs.local_features) as sift:
            self.index.build(vs.StandardEncoder())
            self.index.build(FakeDino('', lambda: False))
            self.assertEqual(sift.call_count, 2)
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM visual_local_features').fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT count(*) FROM visual_features WHERE local_data IS NOT NULL').fetchone()[0], 0)
        backup = Path(self.tmp.name)/'backup.sqlite3'
        self.db.backup(backup)
        report = vs.SearchIndex(Catalog(backup)).search(png(picture(6)))
        self.assertEqual(report.indexed, 0)
        self.assertEqual(report.hits[0]['page_number'], 2)
        self.assertGreaterEqual(report.hits[0]['inliers'], 8)
        self.db.save_thumb(row, png(picture(7)))
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM visual_local_features').fetchone()[0], 0)
        self.index.build(vs.StandardEncoder())
        with self.db.connect() as c:
            c.execute('DELETE FROM files')
            self.assertEqual(c.execute('SELECT count(*) FROM visual_local_features').fetchone()[0], 0)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_stale_revision_cannot_save_shared_sift(self):
        row = self.add('race.png', [picture(9)])
        db = self.db
        class Updating(vs.StandardEncoder):
            def encode(self, image, multiple=True):
                db.save_thumb(row, png(picture(10)))
                return super().encode(image, multiple)
        self.index.build(Updating())
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM visual_local_features').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM visual_features').fetchone()[0], 0)

    def test_many_files_use_bounded_shared_commits_and_callback_after_commit(self):
        for n in range(5): self.add(str(n)+'.png', [picture(n)])
        connect, commits, done = self.db.connect, [], []
        @contextmanager
        def traced():
            with connect() as c:
                c.set_trace_callback(lambda sql: commits.append(sql) if sql == 'COMMIT' else None)
                yield c
        def file_done(uid):
            with connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM visual_features WHERE registration_uid=?', (uid,)).fetchone()[0], 1)
            done.append(uid)
        with patch.object(self.db, 'connect', traced):
            self.assertEqual(self.index.build(vs.StandardEncoder(), file_progress=file_done), 5)
        self.assertEqual(len(commits), 1)
        self.assertEqual(set(done), self.index.thumbnail_uids())

    def test_model_failure_flushes_previous_pages_without_poisoning_next(self):
        self.add('many.tif', [picture(1), picture(2), picture(3)])
        class FailsSecond(vs.StandardEncoder):
            count = 0
            def encode(self, image, multiple=True):
                self.count += 1
                if self.count == 2: raise RuntimeError('model failed')
                return super().encode(image, multiple)
        with self.assertRaisesRegex(RuntimeError, 'model failed'):
            self.index.build(FailsSecond())
        self.assertEqual(len(self.snapshot()), 1)
        self.assertEqual(self.index.build(vs.StandardEncoder()), 2)


class WholeJobProgressTests(unittest.TestCase):
    setUpClass = classmethod(indexing_tests.AutomaticFeatureTests.setUpClass.__func__)
    setUp = indexing_tests.AutomaticFeatureTests.setUp
    stop_panels = indexing_tests.AutomaticFeatureTests.stop_panels
    wait_until = indexing_tests.AutomaticFeatureTests.wait_until
    panel = indexing_tests.AutomaticFeatureTests.panel
    count = indexing_tests.AutomaticFeatureTests.count

    def add_many(self, count):
        for i in range(count): (self.root/(str(i)+'.png')).write_bytes(b'placeholder')
        self.db.scan(self.db.roots()[0])
        data = png(Image.new('RGB', (64, 48), 'gray'))
        for row in self.db.rows(): self.db.save_thumb(row, data)
        return self.db.rows()

    def test_progress_includes_saved_files_and_crosses_32_without_reset(self):
        rows = self.add_many(67)
        cached = [r['registration_uid'] for r in rows[:2]]
        self.index.build(vs.StandardEncoder(), uids=cached)
        self.index.build(FakeDino('', lambda: False), uids=cached)
        panel, messages = self.panel(), []
        panel.worker.status.connect(lambda text, values: messages.append((text, values)))
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), patch('feature_indexing.DinoEncoder', FakeDino):
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            panel.prompt.done(QMessageBox.StandardButton.Yes)
            self.wait_until(lambda: '特徴抽出完了: 全体' in panel.label.text(), seconds=25)
        progress = [v for text, v in messages if '特徴抽出中:' in text]
        self.assertEqual({v['total'] for v in progress}, {67})
        counts = [v['done'] for v in progress]
        self.assertEqual(counts[0], 2)
        self.assertEqual(counts[-1], 67)
        self.assertEqual(counts, sorted(counts))
        self.assertIn(33, counts)
        self.assertIn(65, counts)
        self.assertEqual(sum('特徴抽出完了: 全体' in t for t, _ in messages), 1)
        self.assertEqual(self.count(FakeDino.key), 67)

    def test_queue_growth_duplicates_and_cancel_start_fresh_job(self):
        probe = FeatureWorker(self.db)
        probe.enqueue(['one', 'two'])
        probe.enqueue(['two', 'three'])
        self.assertEqual(len(probe.job_uids), 3)
        self.assertEqual(len(probe.pending), 3)
        probe.job_done.add('one')
        probe.enqueue(['four'])
        self.assertEqual(len(probe.job_uids), 4)
        self.assertEqual(probe.job_done, {'one'})
        probe.enqueue(['one'])  # A regenerated thumbnail needs DINO features again.
        self.assertFalse(probe.job_done)
        serial = probe.job_serial
        probe.cancel()
        probe.enqueue(['new'])
        self.assertGreater(probe.job_serial, serial)
        self.assertEqual(probe.job_uids, {'new'})
        self.assertEqual(list(probe.pending), ['new'])

    def test_failure_does_not_report_all_files_complete(self):
        rows = self.add_many(3)
        panel = self.panel()
        with patch.object(panel.worker, 'available', return_value=(SPECS, {})), \
             patch('feature_indexing.DinoEncoder', side_effect=RuntimeError('model load failed')):
            panel.start()
            self.wait_until(lambda: panel.prompt is not None)
            panel.prompt.done(QMessageBox.StandardButton.Yes)
            self.wait_until(lambda: '特徴抽出を一部保留: 全体' in panel.label.text())
            self.assertEqual(panel.state[1], dict(done=0, total=3))
            self.assertEqual(self.count(vs.StandardEncoder.key), 0)
            self.assertEqual(self.count(FakeDino.key), 0)


@unittest.skipUnless(os.environ.get('MEDIA_CATALOG_TEST_DINO'), 'optional local DINOv2 model')
class RealDinoBatchTests(unittest.TestCase):
    def test_batch_matches_legacy_vectors_and_single_query(self):
        import socket
        with patch.object(socket, 'socket', side_effect=AssertionError('Network access')):
            encoder = vs.DinoEncoder(os.environ['MEDIA_CATALOG_TEST_DINO'])
            for image in (picture(18), Image.new('RGB', (32, 28), 'gray')):
                for multiple in (True, False):
                    old = []
                    for part in (vs.regions(image) if multiple else [image]):
                        inputs = encoder.processor(images=part.resize((224, 224)), return_tensors='pt',
                                                   do_resize=False, do_center_crop=False)
                        with encoder.torch.inference_mode():
                            vector = encoder.model(**inputs).last_hidden_state[:, 0].float().cpu().numpy()[0]
                        old.append(vs.unit(vector))
                    with patch.object(encoder.model, 'forward', wraps=encoder.model.forward) as forward:
                        batch = encoder.encode(image, multiple)
                        self.assertEqual(forward.call_count, 1)
                    np.testing.assert_allclose(batch, np.stack(old), rtol=1e-4, atol=2e-6)
                    self.assertEqual(batch.dtype, np.float32)
            encoder.stop = lambda: True
            with self.assertRaises(vs.SearchCancelled): encoder.encode(picture(18))


if __name__ == '__main__': unittest.main()
