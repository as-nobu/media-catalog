"""Stable image and caption slots, independent of thumbnail aspect ratio."""
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QIcon, QColor, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyle, QStyleOptionViewItem


class PageBadgeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        view = option.widget or self.parent()
        grid = view.gridSize()
        if grid.isValid() and not grid.isEmpty():
            return grid
        icon = view.iconSize()
        return QSize(icon.width()+30, icon.height()+2*option.fontMetrics.lineSpacing()+24)

    @staticmethod
    def slots(option, icon_size):
        inner = option.rect.adjusted(8, 6, -8, -6)
        text_height = min(inner.height(), 2*option.fontMetrics.lineSpacing())
        text = QRect(inner.x(), inner.bottom()-text_height+1, inner.width(), text_height)
        height = max(0, min(icon_size.height(), text.top()-inner.top()-4))
        width = max(0, min(icon_size.width(), inner.width()))
        image = QRect(inner.x()+(inner.width()-width)//2, inner.y(), width, height)
        return image, text

    @staticmethod
    def caption_lines(text, font, metrics, width):
        # Qt's initStyleOption converts newlines to Unicode line separators.
        text = text.replace('\u2028', '\n').replace('\u2029', '\n')
        if '\n' in text:
            # Search results reserve their second line for the matching page.
            return [metrics.elidedText(part.replace('\n', ' '), Qt.TextElideMode.ElideRight, width)
                    for part in text.split('\n', 1)]
        layout = QTextLayout(text, font)
        settings = QTextOption()
        settings.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(settings)
        layout.beginLayout()
        try:
            line = layout.createLine()
            if not line.isValid():
                return []
            line.setLineWidth(max(1, width))
            # QTextLine indexes are UTF-16 offsets, not Python codepoint indexes.
            units, offset = 0, 0
            while offset < len(text) and units < line.textLength():
                units += 2 if ord(text[offset]) > 0xffff else 1
                offset += 1
            first, rest = text[:offset], text[offset:]
            return [first] + ([metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width)] if rest else [])
        finally:
            layout.endLayout()

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget or self.parent()
        image_rect, text_rect = self.slots(opt, view.iconSize())
        background = QStyleOptionViewItem(opt)
        background.text = ''
        background.icon = QIcon()
        background.features &= ~(QStyleOptionViewItem.ViewItemFeature.HasDisplay |
                                 QStyleOptionViewItem.ViewItemFeature.HasDecoration)
        style = view.style() if view is not None else QApplication.style()
        painter.save()
        try:
            painter.setClipRect(opt.rect)
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, view)
            opt.icon.paint(painter, image_rect, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal)
            painter.setFont(opt.font)
            role = QPalette.ColorRole.HighlightedText if opt.state & QStyle.StateFlag.State_Selected else QPalette.ColorRole.Text
            painter.setPen(opt.palette.color(role))
            lines = self.caption_lines(opt.text, opt.font, opt.fontMetrics, text_rect.width())
            for number, text in enumerate(lines):
                line_rect = QRect(text_rect.x(), text_rect.y()+number*opt.fontMetrics.lineSpacing(),
                                  text_rect.width(), opt.fontMetrics.lineSpacing())
                painter.drawText(line_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                                 text)
            row = index.model().items[index.row()]
            count = row.get('page_count', 1)
            if count > 1 and 'page_number' not in row:
                text = f"{row['search_page']} / {count} p" if 'search_page' in row else f'{count} p'
                width = opt.fontMetrics.horizontalAdvance(text)+16
                rect = QRect(opt.rect.right()-width-8, opt.rect.top()+6, width, 24)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor('#25364b'))
                painter.drawRoundedRect(rect, 4, 4)
                painter.setPen(QColor('white'))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        finally:
            painter.restore()
