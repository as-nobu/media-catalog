import builtins
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from catalog import Catalog
from thumbnail_worker import generate


class SafetyTests(unittest.TestCase):
    def test_original_read_only_and_decoder_receives_copy(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            source = base/'original.png'
            Image.new('RGB',(80,60),'blue').save(source)
            before = (hashlib.sha256(source.read_bytes()).digest(),source.stat().st_mtime_ns)
            original_open = builtins.open
            seen = []
            def guarded(path, mode='r', *args, **kwargs):
                if isinstance(path,(str,os.PathLike)) and os.path.abspath(path)==str(source):
                    seen.append(mode)
                    self.assertEqual(mode,'rb')
                return original_open(path,mode,*args,**kwargs)
            real_image_open = Image.open
            def image_open(path,*args,**kwargs):
                self.assertNotEqual(os.path.abspath(path),str(source))
                return real_image_open(path,*args,**kwargs)
            with patch('builtins.open',side_effect=guarded), patch('thumbnail_worker.Image.open',side_effect=image_open):
                generate(str(source),'image',str(base/'thumb.jpg'))
                generate(str(source),'copy',str(base/'view.png'))
            self.assertEqual(seen,['rb','rb'])
            self.assertEqual(before,(hashlib.sha256(source.read_bytes()).digest(),source.stat().st_mtime_ns))
            self.assertEqual(source.read_bytes(),(base/'view.png').read_bytes())
            with self.assertRaises(ValueError):
                generate(str(source),'image',str(source))

    def test_four_directories_are_scanned_concurrently(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            root = base/'source'
            root.mkdir()
            for i in range(4):
                folder=root/f'd{i}'
                folder.mkdir()
                (folder/'a.jpg').write_bytes(b'x')
            db=Catalog(base/'state'/'catalog.db')
            db.add_root(root)
            barrier=threading.Barrier(4)
            threads=set()
            lock=threading.Lock()
            original=os.scandir
            def concurrent(path):
                if Path(path).name.startswith('d'):
                    with lock:
                        threads.add(threading.get_ident())
                    barrier.wait(timeout=5)
                return original(path)
            with patch('catalog.os.scandir',side_effect=concurrent):
                count,errors=db.scan(db.roots()[0],workers=4)
            self.assertEqual(errors,[])
            self.assertEqual(count,4)
            self.assertEqual(len(threads),4)

    def test_slow_download_does_not_block_other_jobs_or_scan_and_times_out(self):
        from PySide6.QtCore import QCoreApplication
        from app import Service
        qt = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            root=base/'source'
            root.mkdir()
            for name in ('slow.jpg','fast.jpg'):
                Image.new('RGB',(30,30),'red').save(root/name)
            db=Catalog(base/'state'/'catalog.db')
            db.add_root(root)
            db.scan(db.roots()[0])
            with db.connect() as c:
                c.execute('UPDATE files SET changed_at=0')
            service=Service(db,Path(__file__).with_name('slow_worker.py'))
            service.timeout=5
            service.start()
            def until(predicate,seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    if predicate():
                        return True
                    time.sleep(.05)
                return False
            try:
                fast=next(r for r in db.rows() if r['name']=='fast.jpg')
                self.assertTrue(until(lambda:db.thumbnail(fast['id']) is not None,4))
                slow=next(r for r in db.rows() if r['name']=='slow.jpg')
                self.assertIsNone(db.thumbnail(slow['id']))
                Image.new('RGB',(30,30),'green').save(root/'late.png')
                service.request_scan()
                self.assertTrue(until(lambda:len(db.rows())==3,2))
                self.assertTrue(until(lambda:any('タイムアウト' in r['error'] for r in db.rows()),6))
                self.assertTrue((root/'slow.jpg').exists())
            finally:
                service.stop()
                self.assertTrue(service.wait(10000))


if __name__=='__main__':
    unittest.main()
