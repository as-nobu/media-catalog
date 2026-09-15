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
    ensure_qt()
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    document = QPdfDocument(None)
    try:
        error = document.load(str(source))
        if error==QPdfDocument.Error.IncorrectPassword:
            raise RuntimeError('パスワード付きAIのため、自動生成をスキップしました。')
        if error!=QPdfDocument.Error.None_ or document.pageCount()<1:
            raise RuntimeError('AIのPDF互換データを読み込めません。')
        def pages():
            for index in range(min(document.pageCount(),MAX_PAGE_THUMBNAILS)):
                size = document.pagePointSize(index)
                if size.isEmpty():
                    raise RuntimeError('ベクター画像を描画できません。')
                scale = 512/max(size.width(),size.height())
                yield pillow_image(document.render(index,QSize(max(1,round(size.width()*scale)),max(1,round(size.height()*scale)))))
        save_pages(pages(),output,metadata={'形式':'Adobe Illustrator (PDF)'},total_pages=document.pageCount())
    finally:
        document.close()
