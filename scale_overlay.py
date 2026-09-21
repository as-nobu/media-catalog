"""Draw calibrated scale bars on display copies, never on cached thumbnails."""
import json
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QColor
from tiff_scale import nice_bar, saved_scale


def page_scale(row,page=1):
    try:
        metadata=json.loads(row.get('metadata_json','{}'))
        return saved_scale(metadata,page) if isinstance(metadata,dict) else {}
    except (ValueError,TypeError,KeyError): return {}



def overlay(image,scale):
    bar=nice_bar(scale,image.width())
    if bar is None or image.height()<55: return image
    result=image.copy()
    painter=QPainter(result)
    try:
        font=painter.font();font.setPixelSize(12);painter.setFont(font)
        length,label=bar
        width=max(round(length)+16,painter.fontMetrics().horizontalAdvance(label)+16)
        if width>image.width()-8: return image
        box=QRect(image.width()-width-5,image.height()-43,width,38)
        painter.fillRect(box,QColor(0,0,0,185))
        painter.setPen(QColor('white'))
        painter.drawText(QRect(box.x(),box.y()+2,width,19),Qt.AlignmentFlag.AlignCenter,label)
        x=box.x()+(width-round(length))//2
        painter.fillRect(QRect(x,box.y()+26,max(1,round(length)),3),QColor('white'))
    finally: painter.end()
    return result
