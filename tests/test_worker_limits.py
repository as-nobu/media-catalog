import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from thumbnail_worker import configure_image_limit,find_ffmpeg,generate


class WorkerLimitsTests(unittest.TestCase):
    def test_reported_pixel_count_and_finite_limit(self):
        previous=Image.MAX_IMAGE_PIXELS
        try:
            with patch.dict(os.environ,{'MEDIA_CATALOG_MAX_IMAGE_MP':'300'}):
                configure_image_limit()
                Image._decompression_bomb_check((183805972,1))
                with self.assertRaises(Image.DecompressionBombError):
                    Image._decompression_bomb_check((300000001,1))
        finally:
            Image.MAX_IMAGE_PIXELS=previous

    def test_conda_ffmpeg_and_missing_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.dict(os.environ,{'CONDA_PREFIX':temp,'MEDIA_CATALOG_FFMPEG':''}),patch('thumbnail_worker.sys.prefix',temp),patch('thumbnail_worker.shutil.which',return_value=None):
                with patch('thumbnail_worker.copy_source',side_effect=AssertionError('unnecessary hydration')):
                    with self.assertRaisesRegex(RuntimeError,'FFmpeg'):
                        generate(str(root/'cloud.mp4'),'video',root/'thumb.jpg')
                (root/'Library'/'bin').mkdir(parents=True)
                exe=root/'Library'/'bin'/'ffmpeg.exe'
                exe.touch()
                self.assertEqual(find_ffmpeg(),str(exe))


if __name__=='__main__':
    unittest.main()
