import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,TiffImagePlugin
from thumbnail_worker import image_metadata,generate
from tiff_scale import nice_bar,ome_scales

class TiffScaleTests(unittest.TestCase):
    def test_standard_and_ome_priority_and_units(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'sample.tif'
            tags=TiffImagePlugin.ImageFileDirectory_v2()
            tags[282]=20000;tags[283]=10000;tags[296]=3
            Image.new('RGB',(100,80)).save(p,tiffinfo=tags)
            scale=image_metadata(p)['page_scales'][0]
            self.assertEqual((scale['x'],scale['y']),(.5,1))
            tags[270]='<OME><Image><Pixels SizeX="100" SizeY="80" PhysicalSizeX="250" PhysicalSizeXUnit="nm" PhysicalSizeY="0.5"><TiffData/></Pixels></Image></OME>'
            Image.new('RGB',(100,80)).save(p,tiffinfo=tags)
            scale=image_metadata(p)['page_scales'][0]
            self.assertEqual((scale['x'],scale['y'],scale['source']),(.25,.5,'OME-TIFF'))
            tags[296]=1;del tags[270]
            Image.new('RGB',(100,80)).save(p,tiffinfo=tags)
            self.assertIsNone(image_metadata(p)['page_scales'][0]['x'])

    def test_page_calibration_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'sample.tif';out=Path(temp)/'out.jpg'
            frames=[Image.new('RGB',(100,80)) for _ in range(21)]
            frames[0].save(p,save_all=True,append_images=frames[1:],dpi=(25400,12700))
            original=p.read_bytes();generate(p,'image',out)
            scales=json.loads(Path(str(out)+'.json').read_text())['metadata']['page_scales']
            self.assertEqual(len(scales),20)
            self.assertEqual((scales[19]['x'],scales[19]['y']),(1,2))
            self.assertEqual(p.read_bytes(),original)

    def test_mapping_and_nice_lengths(self):
        xml='<OME UUID="local"><Image><Pixels SizeX="100" SizeY="80" PhysicalSizeX="1"><TiffData IFD="2" PlaneCount="2"/></Pixels></Image></OME>'
        self.assertEqual(set(ome_scales(xml,20)),{2,3})
        self.assertEqual(ome_scales('<broken',20),{})
        self.assertEqual(nice_bar({'x':.5,'width':1000},500),(100,'100 µm'))
        self.assertIsNone(nice_bar({'x':None,'width':1000},500))

    def test_legacy_cached_metadata_without_file_access(self):
        from unittest.mock import patch
        from tiff_scale import saved_scale
        metadata={'形式':'TIFF','幅 (px)':100,'高さ (px)':80,
                  'EXIF.XResolution':'20000/1','EXIF.YResolution':'10000.0',
                  'EXIF.ResolutionUnit':'3'}
        with patch('builtins.open',side_effect=AssertionError('source access')):
            scale=saved_scale(metadata)
            self.assertEqual((scale['x'],scale['y']),(.5,1))
            self.assertEqual(saved_scale(metadata,2),{})
            metadata['EXIF.ImageDescription']='<OME><Image><Pixels SizeX="100" SizeY="80" PhysicalSizeX="250" PhysicalSizeXUnit="nm" PhysicalSizeY="0.5"><TiffData/></Pixels></Image></OME>'
            self.assertEqual(saved_scale(metadata)['x'],.25)
            metadata['EXIF.ImageDescription']='<OME><Image><Pixels'
            self.assertEqual(saved_scale(metadata)['x'],.5)
            metadata['EXIF.ResolutionUnit']='1'
            self.assertIsNone(saved_scale(metadata)['x'])
            metadata['page_scales']=[{'x':2,'y':3,'width':100,'height':80}]
            self.assertEqual(saved_scale(metadata)['x'],2)


    def test_actual_legacy_metadata(self):
        from tiff_scale import saved_scale
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'sample.tif'
            Image.new('RGB',(128,64)).save(source,dpi=(25400,12700))
            metadata=image_metadata(source)
            metadata.pop('page_scales')
            source.unlink() # Prove cached metadata is sufficient after source removal.
            scale=saved_scale(metadata)
            self.assertEqual((scale['x'],scale['y'],scale['width']),(1,2,128))
