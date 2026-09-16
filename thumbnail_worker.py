"""Isolated, timeout-controlled source-content reader. Writes JPEG to output."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import json
import sys
import warnings
from itertools import islice
from limits import MAX_PAGE_THUMBNAILS, EMPTY_FILE_KINDS
from PIL import Image, ImageOps, ExifTags


def configure_image_limit():
    # Isolated worker only. Keep a finite limit; Pillow's error threshold is 2x.
    megapixels = int(os.environ.get('MEDIA_CATALOG_MAX_IMAGE_MP','300'))
    if not 1 <= megapixels <= 2000:
        raise ValueError('MEDIA_CATALOG_MAX_IMAGE_MP must be between 1 and 2000.')
    Image.MAX_IMAGE_PIXELS = megapixels * 1_000_000 // 2
    warnings.filterwarnings('ignore',category=Image.DecompressionBombWarning)
    return megapixels


def find_ffmpeg():
    configured = os.environ.get('MEDIA_CATALOG_FFMPEG')
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError('MEDIA_CATALOG_FFMPEG does not point to an existing FFmpeg executable.')
    found = shutil.which('ffmpeg')
    if found:
        return found
    # Conda on Windows can omit Library/bin from PATH when launched via python.exe.
    for root in dict.fromkeys((sys.prefix,os.environ.get('CONDA_PREFIX',''))):
        if not root:
            continue
        for relative in ('Library/bin/ffmpeg.exe','Scripts/ffmpeg.exe','bin/ffmpeg','ffmpeg.exe'):
            candidate = Path(root)/relative
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError('FFmpegが見つかりません。使用中のConda環境で conda install -c conda-forge ffmpeg を実行し、アプリを再起動してください。')


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
    # Scientific TIFFs use integer/float modes unsupported by LANCZOS in
    # some Pillow versions. Preserve dynamic range until the small preview.
    if image.mode.startswith('I') or image.mode == 'F':
        import numpy as np
        page = image.convert('F')
        page.thumbnail(size,Image.Resampling.LANCZOS)
        page = ImageOps.exif_transpose(page)
        values = np.asarray(page).copy()
        finite = np.isfinite(values)
        preview = np.zeros(values.shape,dtype=np.uint8)
        if finite.any():
            low, high = float(values[finite].min()), float(values[finite].max())
            if high > low:
                preview[finite] = np.clip((values[finite].astype(np.float64)-low)*255/(high-low),0,255).astype(np.uint8)
        return Image.fromarray(preview).convert('RGB')
    # Shrink before the EXIF copy/rotation to avoid another full-resolution buffer.
    image.thumbnail(size,Image.Resampling.LANCZOS)
    page = ImageOps.exif_transpose(image)
    rgba = page.convert('RGBA')
    rgb = Image.new('RGB',rgba.size,'white')
    rgb.paste(rgba,mask=rgba.getchannel('A'))
    return rgb


def check_powerpoint(source):
    """Reject confirmed encryption; unknown formats may be tried by PowerPoint."""
    try:
        import msoffcrypto
    except ImportError as exc:
        raise RuntimeError('PPTの事前検査に必要なライブラリがありません。pip install -r requirements.txt を実行してください。') from exc
    try:
        with open(source,'rb') as stream:
            encrypted = msoffcrypto.OfficeFile(stream).is_encrypted()
    except Exception:
        return None
    if encrypted:
        raise RuntimeError('パスワード付きPPTのため、自動生成をスキップしました。')
    return False


def read_pages(path):
    with Image.open(path) as im:
        limit = im.n_frames if im.format=='TIFF' else 1
        for i in range(min(limit,MAX_PAGE_THUMBNAILS)):
            im.seek(i)
            yield fit_page(im,(512,512))


def export_slides(presentation, temp):
    count = presentation.Slides.Count
    if count<1:
        raise RuntimeError('スライドがありません。')
    width, height = presentation.PageSetup.SlideWidth, presentation.PageSetup.SlideHeight
    scale = 512/max(width,height)
    for i in range(1,min(count,MAX_PAGE_THUMBNAILS)+1):
        path = str(Path(temp)/f'slide-{i}.png')
        presentation.Slides(i).Export(path,'PNG',max(1,round(width*scale)),max(1,round(height*scale)))
        yield path


def save_pages(pages,output,metadata=None,total_pages=None):
    folder = Path(str(output)+'.pages')
    folder.mkdir(exist_ok=True)
    count = 0
    for count,page in enumerate(islice(pages,MAX_PAGE_THUMBNAILS),1):
        page.save(folder/f'{count}.jpg','JPEG',quality=85)
        if count==1:
            page.save(output,'JPEG',quality=85)
    if not count:
        raise ValueError('表示できるページがありません。')
    Path(str(output)+'.json').write_text(json.dumps({'page_count':total_pages if total_pages is not None else count,'thumbnail_count':count,'metadata':metadata},ensure_ascii=False),encoding='utf-8')


def generate(source, kind, output):
    configure_image_limit()
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(output)):
        raise ValueError('元ファイルを出力先にはできません。')
    if kind == 'copy':
        copy_source(source,output)
        return
    if kind in EMPTY_FILE_KINDS and os.stat(source).st_size == 0:
        save_empty(output,'空ファイル（0 bytes）')
        return
    ffmpeg = find_ffmpeg() if kind == 'video' else None
    with tempfile.TemporaryDirectory(prefix='decode-',dir=Path(output).parent) as temp:
        staged = str(Path(temp)/('source'+Path(source).suffix))
        copy_source(source,staged)
        # All decoders / Office see only the private local copy.
        source = staged
        metadata = None
        if kind=='svg':
            from vector_preview import svg_page
            save_pages([svg_page(source)],output,metadata={'形式':'SVG'})
            return
        if kind=='illustrator':
            from vector_preview import save_illustrator
            save_illustrator(source,output,save_pages)
            return
        if kind == 'image':
            metadata = image_metadata(source)
            pages = read_pages(source)
        elif kind == 'video':
            image_path = str(Path(temp)/'frame.png')
            # First decodable frame; no whole-file probing or hashing.
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y',
                '-i',source,'-frames:v','1','-vf','scale=512:512:force_original_aspect_ratio=decrease',image_path],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            pages = read_pages(image_path)
        else:
            check_powerpoint(source)
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
                save_pages(pages,output,total_pages=presentation.Slides.Count)
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
        total = metadata['フレーム数'] if kind=='image' and metadata['形式']=='TIFF' else None
        save_pages(pages,output,metadata,total_pages=total)


def save_empty(output,reason):
    Path(str(output)+'.json').write_text(json.dumps({'page_count':0,'metadata':{'状態':reason}},ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('kind')
    parser.add_argument('output')
    args = parser.parse_args()
    try:
        generate(args.source,args.kind,args.output)
    except Image.DecompressionBombError:
        print('画像の画素数が設定上限を超えています。必要なら MEDIA_CATALOG_MAX_IMAGE_MP を増やしてください（既定300MP）。',file=sys.stderr)
        sys.exit(1)
    except MemoryError:
        print('画像の展開に必要なメモリが不足しています。他のアプリを閉じて再試行してください。',file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(1)
