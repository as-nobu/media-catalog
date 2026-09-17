"""Render staged vector files without launching their editing applications."""
import io
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
from limits import MAX_PAGE_THUMBNAILS

_application = None


def ensure_qt():
    global _application
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtGui import QGuiApplication
    _application = QGuiApplication.instance() or QGuiApplication([])


def pillow_image(image):
    from PySide6.QtCore import QBuffer, QIODevice
    if image.isNull():
        raise RuntimeError('ベクター画像を描画できません。')
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer,'PNG'):
        raise RuntimeError('ベクター画像を描画できません。')
    with Image.open(io.BytesIO(bytes(buffer.data()))) as page:
        rgba = page.convert('RGBA')
        rgb = Image.new('RGB',page.size,'white')
        rgb.paste(rgba,mask=rgba.getchannel('A'))
        return rgb


def svg_page(source):
    # Re-serialize XML so no processing instructions or DTD reach the renderer.
    root = ET.fromstring(Path(source).read_bytes())
    if root.tag.split('}')[-1]!='svg':
        raise RuntimeError('SVGを読み込めません。')
    for element in root.iter():
        element.attrib.pop('{http://www.w3.org/XML/1998/namespace}base',None)
        for key,value in element.attrib.items():
            if key.split('}')[-1]=='href' and value.strip() and not value.strip().startswith(('#','data:image/png;base64,','data:image/jpeg;base64,')):
                raise RuntimeError('外部リンクを含むSVGは自動生成できません。画像を埋め込んで保存してください。')
        css = ' '.join(element.attrib.values())+' '+(element.text or '')
        if '@import' in css.lower() or '\\' in css or any(not v.strip(' \t\r\n\"\'').startswith('#') for v in re.findall(r'url\s*\((.*?)\)',css,re.I|re.S)):
            raise RuntimeError('外部リンクを含むSVGは自動生成できません。画像を埋め込んで保存してください。')
    ensure_qt()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(ET.tostring(root,encoding='utf-8'))
    if not renderer.isValid():
        raise RuntimeError('SVGを読み込めません。')
    size = renderer.defaultSize()
    if size.isEmpty():
        raise RuntimeError('SVGのサイズが不正です。')
    size.scale(512,512,Qt.AspectRatioMode.KeepAspectRatio)
    image = QImage(size,QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return pillow_image(image)


def save_illustrator(source,output,save_pages):
    with open(source,'rb') as stream:
        if b'%PDF-' not in stream.read(1024):
            raise RuntimeError('PDF互換でないAIファイルには対応していません。IllustratorでPDF互換を有効にして別途保存してください。')
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError('pypdfium2を読み込めません。依存パッケージのインストールを確認してください。') from exc
    try:
        document = pdfium.PdfDocument(str(source))
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise RuntimeError('パスワード付きAIのため、自動生成をスキップしました。') from exc
        raise RuntimeError('AIのPDF互換データを読み込めません。') from exc
    with document:
        total = len(document)
        if total < 1:
            raise RuntimeError('AIのPDF互換データを読み込めません。')
        def pages():
            # Each thumbnail worker is a separate process; PDFium calls stay serial.
            for index in range(min(total, MAX_PAGE_THUMBNAILS)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not (0 < width < float('inf') and 0 < height < float('inf')):
                        raise RuntimeError('ベクター画像を描画できません。')
                    bitmap = page.render(scale=512/max(width, height), fill_color=(255,255,255,255))
                    try:
                        # Detach from PDFium memory before closing the bitmap.
                        image = bitmap.to_pil().convert('RGB').copy()
                        image.thumbnail((512,512), Image.Resampling.LANCZOS)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                yield image
        save_pages(pages(),output,metadata={'形式':'Adobe Illustrator (PDF)'},total_pages=total)
