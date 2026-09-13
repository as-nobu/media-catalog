import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from catalog import Catalog
from thumbnail_worker import generate


class MetadataTests(unittest.TestCase):
    def test_metadata_memo_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            source = p/'source'
            source.mkdir()
            image = source/'photo.jpg'
            exif = Image.Exif()
            exif[271] = 'Test camera'
            exif[306] = '2026:09:13 12:00:00'
            Image.new('RGB',(900,600),'red').save(image,exif=exif,dpi=(300,300))
            original = image.read_bytes()
            db = Catalog(p/'db'/'catalog.sqlite3')
            db.add_root(source)
            db.scan(db.roots()[0])
            row = db.rows()[0]
            self.assertTrue(db.save_memo(row['registration_uid'],'検体A\n測定メモ',''))
            self.assertFalse(db.save_memo(row['registration_uid'],'競合',''))
            output = p/'thumb.jpg'
            generate(str(image),'image',str(output))
            manifest = json.loads(Path(str(output)+'.json').read_text())
            meta = manifest['metadata']
            self.assertEqual(meta['幅 (px)'],900)
            self.assertEqual(meta['EXIF.Make'],'Test camera')
            self.assertTrue(db.save_thumb(row,output.read_bytes(),metadata=meta))
            db = Catalog(db.path)
            with patch('builtins.open',side_effect=AssertionError('source read')):
                saved = db.file_record(row['registration_uid'])
                self.assertEqual(saved['memo'],'検体A\n測定メモ')
                self.assertEqual(len(db.rows(search='検体A')),1)
                self.assertEqual(json.loads(saved['metadata_json']),meta)
            db.scan(db.roots()[0])
            self.assertEqual(db.rows()[0]['memo'],saved['memo'])
            db.remove_folder(source)
            db.add_root(source)
            db.scan(db.roots()[0])
            self.assertFalse(db.save_memo(row['registration_uid'],'late',saved['memo']))
            self.assertEqual(db.rows()[0]['memo'],'')
            self.assertEqual(image.read_bytes(),original)


if __name__ == '__main__':
    unittest.main()
