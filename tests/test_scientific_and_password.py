import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image
import msoffcrypto
from msoffcrypto.format.ooxml import OOXMLFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thumbnail_worker import generate, fit_page, check_powerpoint


class ScientificAndPasswordTests(unittest.TestCase):
    def test_scientific_tiff_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            for dtype in ('uint16','int32','float32'):
                with self.subTest(dtype=dtype):
                    data = np.tile(np.linspace(1000,4000,800),(600,1)).astype(dtype)
                    source = Path(temp)/f'{dtype}.tif'
                    output = Path(temp)/f'{dtype}.jpg'
                    Image.fromarray(data).save(source)
                    original = source.read_bytes()
                    generate(str(source),'image',str(output))
                    with Image.open(output) as preview:
                        self.assertEqual(preview.mode,'RGB')
                        self.assertEqual(preview.size,(512,384))
                        self.assertLess(preview.getpixel((5,190))[0],20)
                        self.assertGreater(preview.getpixel((505,190))[0],235)
                    self.assertEqual(source.read_bytes(),original)

    def test_constant_and_nonfinite(self):
        for data in (np.full((20,20),500,dtype=np.uint16),
                     np.full((20,20),float('nan'),dtype=np.float32)):
            self.assertEqual(fit_page(Image.fromarray(data),(512,512)).mode,'RGB')

    def test_plain_encrypted_and_invalid_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            plain = Path(temp)/'plain.pptx'
            with zipfile.ZipFile(plain,'w') as archive:
                archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                archive.writestr('ppt/presentation.xml','<presentation/>')
            check_powerpoint(plain)
            protected = Path(temp)/'protected.pptx'
            with plain.open('rb') as stream, protected.open('wb') as dest:
                OOXMLFile(stream).encrypt('test-only-password',dest)
            with self.assertRaisesRegex(RuntimeError,'パスワード付き'):
                check_powerpoint(protected)
            # Must reject before reaching the platform check or importing COM.
            with self.assertRaisesRegex(RuntimeError,'パスワード付き'):
                generate(str(protected),'powerpoint',str(Path(temp)/'out.jpg'))
            invalid = Path(temp)/'invalid.ppt'
            invalid.write_bytes(b'unknown legacy format')
            self.assertIsNone(check_powerpoint(invalid))

    def test_legacy_detector_result(self):
        with tempfile.TemporaryFile() as stream:
            with patch.object(msoffcrypto,'OfficeFile') as detector:
                detector.return_value.is_encrypted.return_value = True
                with patch('builtins.open',return_value=stream):
                    with self.assertRaisesRegex(RuntimeError,'パスワード付き'):
                        check_powerpoint('legacy.ppt')
