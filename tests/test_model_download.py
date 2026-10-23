"""Download readiness, fixed revision, cancellation and concurrent callers."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from download_search_model import (download_model, ensure_model, model_ready,
                                   MODEL_ID, MODEL_REVISION, MODEL_FILES, INCOMPLETE)
from visual_search import SearchCancelled


def tiny_model(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'config.json').write_text('{"model_type":"dinov2"}')
    (folder/'preprocessor_config.json').write_text('{}')
    header = json.dumps({'weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]}}).encode()
    (folder/'model.safetensors').write_bytes(struct.pack('<Q', len(header))+header+b'\x00'*4)


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)/'model'
        # The fake hub keeps default tests independent of optional AI packages.
        self.calls = []
        def snapshot(repo, **kwargs):
            self.calls.append((repo, kwargs))
            tiny_model(kwargs['local_dir'])
        fake_hub = SimpleNamespace(snapshot_download=snapshot)
        self.modules = patch.dict(sys.modules, {
            'huggingface_hub': fake_hub,
            'filelock': SimpleNamespace(FileLock=lambda path: nullcontext())})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.find_spec = patch('download_search_model.importlib.util.find_spec', return_value=object())
        self.find_spec.start()
        self.addCleanup(self.find_spec.stop)

    def test_pinned_download_and_complete_model_reuse(self):
        self.assertFalse(model_ready(self.folder))
        self.assertEqual(download_model(self.folder), self.folder)
        self.assertTrue(model_ready(self.folder))
        self.assertEqual(self.calls[0][0], MODEL_ID)
        self.assertEqual(self.calls[0][1]['revision'], MODEL_REVISION)
        self.assertFalse(self.calls[0][1]['token'])
        self.assertEqual(self.calls[0][1]['allow_patterns'], [*MODEL_FILES, 'README.md'])
        download_model(self.folder)
        with patch('download_search_model.subprocess.Popen', side_effect=AssertionError('Unexpected download')):
            ensure_model(self.folder)
        self.assertEqual(len(self.calls), 1)

    def test_failed_partial_download_not_ready_and_retry(self):
        def fail(*args, **kwargs):
            tiny_model(self.folder)
            raise OSError('offline')
        with patch.object(sys.modules['huggingface_hub'], 'snapshot_download', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'offline'):
                download_model(self.folder)
        self.assertTrue((self.folder/INCOMPLETE).exists())
        self.assertFalse(model_ready(self.folder))
        download_model(self.folder)
        self.assertTrue(model_ready(self.folder))

    def test_truncated_weights_remain_incomplete(self):
        def truncate(*args, **kwargs):
            tiny_model(self.folder)
            path = self.folder/'model.safetensors'
            path.write_bytes(path.read_bytes()[:-1])
        with patch.object(sys.modules['huggingface_hub'], 'snapshot_download', side_effect=truncate):
            with self.assertRaisesRegex(RuntimeError, '不完全'):
                download_model(self.folder)
        self.assertFalse(model_ready(self.folder))

    def spawn_fixture(self, seconds=0, succeed=False):
        """A real child process, with no network, exercises termination/reaping."""
        original = subprocess.Popen
        children = []
        def spawn(args, **kwargs):
            code = ('import sys,time; from pathlib import Path; '
                    'p=Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=True); '
                    f'time.sleep({seconds}); ')
            if succeed:
                code += "[(p/n).write_bytes(b'model') for n in sys.argv[2:]]"
            process = original([sys.executable, '-c', code, str(self.folder), *MODEL_FILES], **kwargs)
            children.append(process)
            return process
        return spawn, children

    def test_cancel_terminates_child_and_releases_lock(self):
        stop = threading.Event()
        spawn, children = self.spawn_fixture(seconds=60)
        timer = threading.Timer(.15, stop.set)
        timer.start()
        try:
            with patch('download_search_model.subprocess.Popen', side_effect=spawn):
                with self.assertRaises(SearchCancelled):
                    ensure_model(self.folder, stop.is_set)
        finally:
            timer.cancel()
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertFalse(model_ready(self.folder))
        spawn, _ = self.spawn_fixture(succeed=True)
        with patch('download_search_model.subprocess.Popen', side_effect=spawn):
            ensure_model(self.folder)
        self.assertTrue(model_ready(self.folder))

    def test_timeout_terminates_child(self):
        spawn, children = self.spawn_fixture(seconds=60)
        with patch('download_search_model.subprocess.Popen', side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, 'タイムアウト'):
                ensure_model(self.folder, timeout=.05)
        self.assertIsNotNone(children[0].poll())

    def test_concurrent_jobs_download_once(self):
        spawn, children = self.spawn_fixture(seconds=.2, succeed=True)
        progress = []
        with patch('download_search_model.subprocess.Popen', side_effect=spawn):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(ensure_model, self.folder, lambda: False,
                                       lambda *a: progress.append(a)) for _ in range(2)]
                for future in futures:
                    self.assertEqual(future.result(timeout=5), self.folder)
        self.assertEqual(len(children), 1)
        self.assertTrue(any(p[0] == 'download' for p in progress))

    def test_missing_downloader_package_does_not_spawn(self):
        with patch('download_search_model.importlib.util.find_spec', return_value=None), \
             patch('download_search_model.subprocess.Popen') as process:
            with self.assertRaisesRegex(RuntimeError, 'huggingface_hub'):
                ensure_model(self.folder)
            process.assert_not_called()

    @unittest.skipUnless(os.environ.get('MEDIA_CATALOG_TEST_DOWNLOAD'), 'optional real network model download')
    def test_real_download_then_offline_reuse(self):
        # Undo dependency fakes for this explicitly enabled integration test.
        self.modules.stop()
        self.find_spec.stop()
        ensure_model(self.folder, timeout=300)
        self.assertTrue(model_ready(self.folder))
        from visual_search import DinoEncoder
        from PIL import Image
        import socket
        with patch.object(socket, 'socket', side_effect=AssertionError('Network on second use')):
            with patch('download_search_model.subprocess.Popen', side_effect=AssertionError('Redownload')):
                ensure_model(self.folder)
            encoder = DinoEncoder(self.folder)
            self.assertEqual(encoder.encode(Image.new('RGB', (64, 64)), multiple=False).shape, (1, 384))


if __name__ == '__main__': unittest.main()
