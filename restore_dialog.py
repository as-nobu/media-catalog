from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,
    QTableWidgetItem,QLineEdit,QPushButton,QFileDialog,QMessageBox,QAbstractItemView)
from catalog_restore import inspect_backup,prepare_restore
from i18n import tr,translate_message


class RestoreDialog(QDialog):
    def __init__(self,source,home,parent=None):
        super().__init__(parent)
        self.source=source;self.home=home;self.busy=False;self.result_path=None
        roots,count=inspect_backup(source)
        self.roots=roots
        self.setWindowTitle(tr('DB復元・フォルダパス変更'));self.resize(950,520)
        layout=QVBoxLayout(self)
        label=QLabel(tr('サムネイル・メモ・タグ・お気に入りを含むDB全体を引き継ぎます。現在のDBは残します。'))
        label.setWordWrap(True);layout.addWidget(label)
        location=QLabel(source);location.setWordWrap(True);layout.addWidget(location)
        self.status=QLabel(tr('ファイル数: {v0}',v0=count));layout.addWidget(self.status)
        table=QTableWidget(len(roots),2)
        table.setHorizontalHeaderLabels([tr('現在の登録パス'),tr('このPCでのパス')]);table.setColumnWidth(0,410)
        table.verticalHeader().setDefaultSectionSize(44)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.paths=[]
        for i,(rid,path) in enumerate(roots):
            item=QTableWidgetItem(path);item.setToolTip(path);table.setItem(i,0,item)
            edit=QLineEdit(path);table.setCellWidget(i,1,edit);self.paths.append(edit)
        layout.addWidget(table,1)
        choose=QPushButton(tr('選択行のフォルダを指定'))
        def browse():
            i=table.currentRow()
            if i<0:return
            path=QFileDialog.getExistingDirectory(self,tr('このPCでのパス'))
            if path:self.paths[i].setText(path)
        choose.clicked.connect(browse);layout.addWidget(choose)
        note=QLabel(tr('適用後は終了します。再起動後は定期スキャン・自動生成をOFFにしてあります。パスを確認してから再開してください。'))
        note.setWordWrap(True);layout.addWidget(note)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        apply=QPushButton(tr('復元して終了'));apply.clicked.connect(self.apply);buttons.addWidget(apply)
        cancel=QPushButton(tr('閉じる'));cancel.clicked.connect(self.reject);buttons.addWidget(cancel)

    def apply(self):
        mapping={rid:edit.text().strip() for (rid,_),edit in zip(self.roots,self.paths)}
        if any(not path for path in mapping.values()):return
        self.busy=True;self.setEnabled(False);self.status.setText(tr('DBのコピー・検証中…'))
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.future=self.pool.submit(prepare_restore,self.source,self.home,mapping)
        self.timer=QTimer(self);self.timer.timeout.connect(self.finish);self.timer.start(100)

    def finish(self):
        if not self.future.done():return
        self.timer.stop();self.pool.shutdown(wait=False);self.busy=False;self.setEnabled(True)
        try:self.result_path=self.future.result()
        except Exception as exc:
            self.status.setText(tr('復元失敗'))
            QMessageBox.warning(self,tr('復元失敗'),translate_message(str(exc)));return
        self.accept()

    def reject(self):
        if not self.busy:super().reject()

    def closeEvent(self,event):
        if self.busy:event.ignore()
        else:super().closeEvent(event)
