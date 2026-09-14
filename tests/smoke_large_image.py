"""Manual regression: ~184 MP, ~551 MB uncompressed RGB TIFF."""
import sys
import tempfile
import json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from thumbnail_worker import generate

with tempfile.TemporaryDirectory() as temp:
    p=Path(temp);source=p/'large.tif';output=p/'thumb.jpg'
    with Image.new('RGB',(14000,13129),(60,120,180)) as image:
        image.save(source,'TIFF',compression='raw')
    before=source.stat()
    generate(str(source),'image',output)
    with Image.open(output) as thumbnail:
        assert max(thumbnail.size)==512
        assert all(abs(a-b)<5 for a,b in zip(thumbnail.getpixel((100,100)),(60,120,180)))
    manifest=json.loads(Path(str(output)+'.json').read_text())
    assert manifest['metadata']['幅 (px)']==14000
    assert manifest['metadata']['高さ (px)']==13129
    assert source.stat().st_size==before.st_size and source.stat().st_mtime_ns==before.st_mtime_ns
print('183,806,000-pixel RGB TIFF to 512px: PASS')
