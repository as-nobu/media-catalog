"""OME unit parsing through Pillow, including UTF-8 TIFF description bytes."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, TiffImagePlugin
from thumbnail_worker import image_metadata
from tiff_scale import nice_bar, ome_scales, saved_scale, UNITS


def ome_xml(unit='\u00b5m'):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
            '<Image Name="sample"><Pixels SizeX="100" SizeY="80" '
            'PhysicalSizeX="0.3771428571428573" PhysicalSizeY="0.5" '
            f'PhysicalSizeXUnit="{unit}" PhysicalSizeYUnit="{unit}">'
            '<TiffData IFD="0" PlaneCount="1"/></Pixels></Image></OME>')


class OmeUnitEncodingTests(unittest.TestCase):
    def test_existing_units_still_work(self):
        for unit, factor in UNITS.items():
            with self.subTest(unit=unit):
                scale = ome_scales(ome_xml(unit), 1)[0]
                self.assertAlmostEqual(scale['x'], 0.3771428571428573*factor)
                self.assertAlmostEqual(scale['y'], .5*factor)

    def test_utf8_bytes_native_text_and_pillow_text_match(self):
        for unit in ('\u00b5m', '\u03bcm', '\u00c5'):
            xml = ome_xml(unit)
            expected = ome_scales(xml, 1)
            with self.subTest(unit=unit):
                self.assertEqual(ome_scales(xml.encode('utf-8'), 1), expected)
                self.assertEqual(ome_scales(xml.encode('utf-8').decode('latin-1'), 1), expected)

    def test_xml_character_reference_and_default_unit(self):
        expected = ome_scales(ome_xml(), 1)
        self.assertEqual(ome_scales(ome_xml('&#181;m'), 1), expected)
        xml = ome_xml().replace(' PhysicalSizeXUnit="\u00b5m"', '').replace(' PhysicalSizeYUnit="\u00b5m"', '')
        self.assertEqual(ome_scales(xml, 1), expected)

    def test_repair_does_not_require_latin1_compatible_image_name(self):
        xml = ome_xml('\u00c2\u00b5m').replace('Name="sample"', 'Name="\u8a66\u6599\U0001f52c"')
        self.assertEqual(ome_scales(xml, 1), ome_scales(ome_xml(), 1))

    def test_unknown_or_damaged_units_are_not_guessed(self):
        for unit in ('pixels', '\u00c2nm', '\ufffdm', '\u00c2\u00b5unknown', 'UM', ''):
            with self.subTest(unit=unit):
                self.assertEqual(ome_scales(ome_xml(unit), 1), {})

    def test_pillow_real_tiff_tag_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'utf8_ome.tif'
            tags = TiffImagePlugin.ImageFileDirectory_v2()
            tags[270] = ome_xml().encode('utf-8')
            tags[282] = tags[283] = 1
            tags[296] = 1
            Image.new('RGB', (100, 80)).save(path, tiffinfo=tags)
            original = path.read_bytes()
            with Image.open(path) as image:
                # Real Pillow converts the two UTF-8 bytes into two characters.
                self.assertIn('\u00c2\u00b5m', image.tag_v2[270])
            scale = image_metadata(path)['page_scales'][0]
            self.assertEqual(scale['source'], 'OME-TIFF')
            self.assertAlmostEqual(scale['x'], 0.3771428571428573)
            self.assertAlmostEqual(scale['y'], .5)
            self.assertEqual(path.read_bytes(), original)

    def test_existing_cached_page_scales_are_not_rewritten(self):
        metadata = {'page_scales': [{'x': None, 'y': None, 'width': 100, 'height': 80}],
                    'EXIF.ImageDescription': ome_xml().encode('utf-8').decode('latin-1')}
        before = copy.deepcopy(metadata)
        with patch('builtins.open', side_effect=AssertionError('source access')), \
             patch.object(Image, 'open', side_effect=AssertionError('source access')):
            self.assertIsNone(saved_scale(metadata)['x'])
        self.assertEqual(metadata, before)

    def test_existing_xml_safeguards_remain(self):
        self.assertEqual(ome_scales('<!DOCTYPE OME>'+ome_xml(), 1), {})
        self.assertEqual(ome_scales('<!ENTITY x "value">'+ome_xml(), 1), {})
        self.assertEqual(ome_scales('<OME><broken', 1), {})
        self.assertEqual(ome_scales(ome_xml().replace('PlaneCount="1"', 'PlaneCount="invalid"'), 1), {})

    @unittest.skipUnless(os.environ.get('MEDIA_CATALOG_SCALE_TEST_IMAGE'), 'optional user sample TIFF')
    def test_rt4m_user_sample(self):
        path = Path(os.environ['MEDIA_CATALOG_SCALE_TEST_IMAGE'])
        before = hashlib.sha256(path.read_bytes()).digest()
        scale = image_metadata(path)['page_scales'][0]
        self.assertEqual((scale['width'], scale['height']), (6233, 5173))
        self.assertAlmostEqual(scale['x'], .3771428571428573)
        self.assertAlmostEqual(scale['y'], .37714285714285734)
        length, label = nice_bar(scale, 512)
        self.assertAlmostEqual(length, 108.90227479349889)
        self.assertEqual(label, '500 \u00b5m')
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)


if __name__ == '__main__':
    unittest.main()
