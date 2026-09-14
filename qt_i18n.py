"""Standard dialog labels following the application language."""
from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication
from i18n import get_language


class DialogTranslator(QTranslator):
    labels = {'&Yes':'はい','Yes':'はい','&No':'いいえ','No':'いいえ',
              'Cancel':'キャンセル','&Cancel':'キャンセル','Save':'保存','&Save':'保存',
              'Discard':'破棄','&Discard':'破棄','Open':'開く','&Open':'開く',
              'Close':'閉じる','&Close':'閉じる','Show Details...':'詳細を表示…',
              'Hide Details...':'詳細を隠す','OK':'OK'}

    def translate(self,context,sourceText,disambiguation=None,n=-1):
        if context in ('QPlatformTheme','QDialogButtonBox','QMessageBox'):
            return self.labels.get(sourceText,sourceText) if get_language()=='ja' else sourceText
        return None


def install_dialog_translator():
    app = QApplication.instance()
    if not hasattr(app,'catalog_translator'):
        app.catalog_translator = DialogTranslator(app)
    else:
        app.removeTranslator(app.catalog_translator)
    app.installTranslator(app.catalog_translator)
