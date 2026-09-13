"""Isolated, timeout-controlled source-content reader. Writes JPEG to output."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import json
from PIL import Image, ImageOps, ExifTags


def image_metadata(path):
    """Read the staged copy only. Never embed binary profiles or thumbnails."""
    def value(v):
        if isinstance(v, bytes):
            return f'[binary: {len(v)} bytes]'
        return str(v)[:4000]
    with Image.open(path) as im:
        result = {'形式': im.format, '幅 (px)': im.width, '高さ (px)': im.height,
                  '色モード': im.mode, 'フレーム数': getattr(im, 'n_frames', 1)}
        for key in ('dpi', 'compression', 'transparency'):
            if key in im.info:
                result[key] = value(im.info[key])
        try:
            exif = im.getexif()
            tags = dict(exif)
            for group in (34665, 34853):
                try:
                    nested = exif.get_ifd(group)
                    names = ExifTags.GPSTAGS if group == 34853 else ExifTags.TAGS
                    for key, v in nested.items():
                        result[('GPS.' if group == 34853 else 'EXIF.') + names.get(key,str(key))] = value(v)
                except Exception:
                    pass
            for key, v in tags.items():
                result['EXIF.' + ExifTags.TAGS.get(key,str(key))] = value(v)
        except Exception as exc:
            result['EXIF取得エラー'] = str(exc)[:500]
        return result


def copy_source(source, output):
    # rb only on originals; xb prevents accidental destination replacement.
    with open(source,'rb') as src, open(output,'xb') as dst:
        shutil.copyfileobj(src,dst,length=1024*1024)


def fit_page(image, size):
    page = ImageOps.exif_transpose(image)
    page.thumbnail(size,Image.Resampling.LANCZOS)
    rgba = page.convert('RGBA')
    rgb = Image.new('RGB',rgba.size,'white')
    rgb.paste(rgba,mask=rgba.getchannel('A'))
    return rgb


def read_pages(path):
    with Image.open(path) as im:
        limit = im.n_frames if im.format=='TIFF' else 1
        for i in range(limit):
            im.seek(i)
            yield fit_page(im,(512,512))


def export_slides(presentation, temp):
    count = presentation.Slides.Count
    if count<1:
        raise RuntimeError('スライドがありません。')
    width, height = presentation.PageSetup.SlideWidth, presentation.PageSetup.SlideHeight
    scale = 512/max(width,height)
    for i in range(1,count+1):
        path = str(Path(temp)/f'slide-{i}.png')
        presentation.Slides(i).Export(path,'PNG',max(1,round(width*scale)),max(1,round(height*scale)))
        yield path


def save_pages(pages,output,metadata=None):
    folder = Path(str(output)+'.pages')
    folder.mkdir(exist_ok=True)
    count = 0
    for count,page in enumerate(pages,1):
        page.save(folder/f'{count}.jpg','JPEG',quality=85)
        if count==1:
            page.save(output,'JPEG',quality=85)
    if not count:
        raise ValueError('表示できるページがありません。')
    Path(str(output)+'.json').write_text(json.dumps({'page_count':count,'metadata':metadata},ensure_ascii=False),encoding='utf-8')


def generate(source, kind, output):
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(output)):
        raise ValueError('元ファイルを出力先にはできません。')
    if kind == 'copy':
        copy_source(source,output)
        return
    if kind == 'powerpoint' and os.stat(source).st_size == 0:
        save_empty(output,'空ファイル（0 bytes）')
        return
    with tempfile.TemporaryDirectory(prefix='decode-',dir=Path(output).parent) as temp:
        staged = str(Path(temp)/('source'+Path(source).suffix))
        copy_source(source,staged)
        # All decoders / Office see only the private local copy.
        source = staged
        metadata = None
        if kind == 'image':
            metadata = image_metadata(source)
            pages = read_pages(source)
        elif kind == 'video':
            image_path = str(Path(temp)/'frame.png')
            # First decodable frame; no whole-file probing or hashing.
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',
                '-i',source,'-frames:v','1','-vf','scale=512:512:force_original_aspect_ratio=decrease',image_path],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            pages = read_pages(image_path)
        else:
            if os.name != 'nt':
                raise RuntimeError('PowerPoint生成にはWindowsとMicrosoft PowerPointが必要です。')
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            app = presentation = None
            try:
                app = win32com.client.DispatchEx('PowerPoint.Application')
                app.AutomationSecurity = 3  # Disable macros in opened presentations.
                presentation = app.Presentations.Open(os.path.abspath(source),ReadOnly=True,Untitled=False,WithWindow=False)
                if presentation.Slides.Count == 0:
                    save_empty(output,'スライドなし')
                    return
                pages = (next(read_pages(path)) for path in export_slides(presentation,temp))
                save_pages(pages,output)
            finally:
                try:
                    if presentation is not None:
                        presentation.Close()
                finally:
                    # Some Office COM servers reuse an existing application.
                    # Never close other presentations the user has open.
                    if app is not None and app.Presentations.Count == 0:
                        app.Quit()
                    pythoncom.CoUninitialize()
            return
        save_pages(pages,output,metadata)


def save_empty(output,reason):
    Path(str(output)+'.json').write_text(json.dumps({'page_count':0,'metadata':{'状態':reason}},ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('kind')
    parser.add_argument('output')
    args = parser.parse_args()
    generate(args.source,args.kind,args.output)
