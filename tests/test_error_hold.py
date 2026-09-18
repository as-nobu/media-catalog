from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import Catalog

class ErrorHoldTests(unittest.TestCase):
    def test_failure_survives_restart_scan_and_file_change_until_manual_retry(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temp:
                base=Path(temp)
                source=base/'source'
                source.mkdir()
                file=source/'test.jpg'
                file.write_bytes(b'fixture')
                db=Catalog(base/'state'/'catalog.db')
                db.add_root(source)
                db.scan(db.roots()[0])
                with db.connect() as c:
                    c.execute('UPDATE files SET changed_at=0')
                job=db.next_job()
                self.assertIsNotNone(job)
                db.fail_job(job,'test failure',timed_out=timeout)
                db=Catalog(base/'state'/'catalog.db')
                for changed in (False, True):
                    if changed:
                        file.write_bytes(b'changed fixture')
                    db.scan(db.roots()[0])
                    with db.connect() as c:
                        c.execute('UPDATE files SET changed_at=0,retry_at=0')
                    self.assertIsNone(db.next_job())
                    self.assertEqual(db.file_record(job['registration_uid'])['error'],'test failure')
                db.regenerate([job['id']])
                self.assertIsNotNone(db.next_job())
