"""Render actual list cells: captions must survive mixed image aspect ratios."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import Qt, QSize, QAbstractListModel, QModelIndex
from PySide6.QtGui import QIcon, QPixmap, QColor, QFontMetrics
from PySide6.QtWidgets import QApplication, QListView, QStyleOptionViewItem
from thumbnail_delegate import PageBadgeDelegate
from theme import STYLE


class Samples(QAbstractListModel):
    def __init__(self):
        super().__init__()
        names=['wide-image.jpg','square-image.jpg','portrait-image.jpg',
               'thumbnail-pending.jpg','very-long-specimen-name-with-details-and-an-emoji-\U0001f9eb.tif',
               '1. matching-file.tif\nMatching page: 3']
        self.items=[dict(name=name,page_count=1) for name in names]
        self.items[1]['page_count']=3
        self.items[-1].update(page_count=5,search_page=3)
        self.icons=[]
        for width,height in [(512,128),(512,512),(128,512),(0,0),(512,512),(512,384)]:
            pix=QPixmap(width,height)
            if width:pix.fill(QColor(220,40,180))
            self.icons.append(QIcon(pix))

    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        if role==Qt.ItemDataRole.DecorationRole:return self.icons[index.row()]
        if role==Qt.ItemDataRole.DisplayRole:return self.items[index.row()]['name']


def pixels(image):
    image=image.convertToFormat(image.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:, :image.width()*4].reshape(image.height(),image.width(),4).copy()


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def make_view(self,large_font=False):
        view=QListView()
        view.setStyleSheet(STYLE+('QListView {font-size:22px;}' if large_font else ''))
        model=Samples();view.setModel(model)
        delegate=PageBadgeDelegate(view);view.setItemDelegate(delegate)
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setUniformItemSizes(True)
        view.setIconSize(QSize(180,140));view.setGridSize(QSize(210,188))
        view.resize(700,650);view.show();view.doItemsLayout()
        self.app.processEvents()
        self.addCleanup(view.close)
        return view,model,delegate

    def test_all_aspect_ratios_have_visible_caption_below_image(self):
        for large_font in [False,True]:
            with self.subTest(large_font=large_font):
                view,model,delegate=self.make_view(large_font)
                view.setCurrentIndex(model.index(0))
                self.app.processEvents()
                canvas=view.viewport().grab().toImage()
                dpr=canvas.devicePixelRatio()
                rectangles=[]
                for row in range(model.rowCount()):
                    index=model.index(row)
                    option=QStyleOptionViewItem()
                    option.initFrom(view);option.widget=view
                    option.rect=view.visualRect(index)
                    option.fontMetrics=QFontMetrics(view.font())
                    image,text=delegate.slots(option,view.iconSize())
                    self.assertLess(image.bottom(),text.top())
                    self.assertTrue(option.rect.contains(text))
                    self.assertEqual(delegate.sizeHint(option,index),QSize(210,188))
                    area=canvas.copy(round(text.x()*dpr),round(text.y()*dpr),
                                     round(text.width()*dpr),round(text.height()*dpr))
                    rgb=pixels(area)[:,:,:3]
                    ink=(rgb[:,:,0]<100)&(rgb[:,:,1]<140)&(rgb[:,:,2]<180)
                    self.assertGreater(int(ink.sum()),5,(large_font,row))
                    if row==5:
                        # Both the filename and matching-page line must paint.
                        split=round(option.fontMetrics.lineSpacing()*dpr)
                        self.assertGreater(int(ink[:split].sum()),5)
                        self.assertGreater(int(ink[split:].sum()),5)
                    image_pixels=(rgb[:,:,0]>180)&(rgb[:,:,1]<100)&(rgb[:,:,2]>140)
                    self.assertEqual(int(image_pixels.sum()),0,(large_font,row))
                    rectangles.append(option.rect.size())
                self.assertTrue(all(size==rectangles[0] for size in rectangles))

    def test_caption_wraps_and_elides_search_page_without_losing_unicode(self):
        view,model,delegate=self.make_view()
        font=view.font();metrics=QFontMetrics(font)
        text='very-long-name-\U0001f9eb-microscopy-sample-with-many-details.tif'
        lines=delegate.caption_lines(text,font,metrics,120)
        self.assertEqual(len(lines),2)
        self.assertTrue(lines[1].endswith('\u2026'))
        for line in lines:self.assertLessEqual(metrics.horizontalAdvance(line),120)
        lines=delegate.caption_lines(model.items[-1]['name'],font,metrics,200)
        self.assertEqual(lines[1],'Matching page: 3')
        option=QStyleOptionViewItem();option.initFrom(view);option.widget=view
        delegate.initStyleOption(option,model.index(5))
        self.assertEqual(delegate.caption_lines(option.text,font,metrics,200),lines)

    def test_late_thumbnail_size_changes_do_not_move_caption_slots(self):
        view,model,delegate=self.make_view()
        before=[view.visualRect(model.index(i)) for i in range(model.rowCount())]
        pix=QPixmap(100,512);pix.fill(QColor(220,40,180))
        model.icons[0]=QIcon(pix)
        model.dataChanged.emit(model.index(0),model.index(0),[Qt.ItemDataRole.DecorationRole])
        self.app.processEvents();view.doItemsLayout()
        self.assertEqual(before,[view.visualRect(model.index(i)) for i in range(model.rowCount())])


if __name__=='__main__':unittest.main()
