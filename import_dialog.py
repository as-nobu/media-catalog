from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QPushButton,QTableWidget,QTableWidgetItem,QAbstractItemView,QPlainTextEdit,
    QMessageBox,QApplication)
from annotation_import import read_source,plan_import,apply_import
from i18n import tr,translate_message


class ImportDialog(QDialog):
    def __init__(self,db,path,parent=None):
        super().__init__(parent)
        self.db=db
        self.source,self.roots=read_source(path)
        self.plan=[]
        self.busy=False
        self.setWindowTitle(tr('メモ・タグの取り込み'))
        self.resize(1000,720)
        layout=QVBoxLayout(self)
        label=QLabel(tr('軽量DB・通常のDBの両方に対応。空欄は絶対パスで照合。別PCでは対応するフォルダを指定してください。'))
        label.setWordWrap(True); layout.addWidget(label)
        layout.addWidget(QLabel(path))
        mapping=QHBoxLayout(); layout.addLayout(mapping)
        self.source_root=QComboBox(); self.source_root.setEditable(True); self.source_root.addItem(''); self.source_root.addItems(self.roots)
        self.target_root=QComboBox(); self.target_root.setEditable(True); self.target_root.addItem(''); self.target_root.addItems([r['path'] for r in db.roots()])
        mapping.addWidget(QLabel(tr('取り込み元フォルダ'))); mapping.addWidget(self.source_root,1)
        mapping.addWidget(QLabel(tr('現在のフォルダ'))); mapping.addWidget(self.target_root,1)
        preview=QPushButton(tr('差分を確認')); preview.clicked.connect(self.preview); mapping.addWidget(preview)
        self.summary=QLabel(); layout.addWidget(self.summary)
        self.table=QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels([tr('ファイル'),tr('タグ（マージ後）'),tr('メモの扱い')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,420); self.table.setColumnWidth(1,220)
        layout.addWidget(self.table,1)
        panes=QHBoxLayout(); layout.addLayout(panes,1)
        self.current=QPlainTextEdit(); self.current.setReadOnly(True)
        self.incoming=QPlainTextEdit(); self.incoming.setReadOnly(True)
        for title,widget in [(tr('現在のメモ'),self.current),(tr('取り込み元のメモ'),self.incoming)]:
            column=QVBoxLayout(); column.addWidget(QLabel(title)); column.addWidget(widget); panes.addLayout(column)
        self.table.currentCellChanged.connect(self.show_notes)
        buttons=QHBoxLayout(); layout.addLayout(buttons)
        note=QLabel(tr('タグは和集合。空欄で既存メモを消しません。適用前に自動バックアップします。'))
        note.setWordWrap(True); buttons.addWidget(note,1)
        self.apply_button=QPushButton(tr('マージを適用')); self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(self.apply_button)
        cancel=QPushButton(tr('閉じる')); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        self.source_root.currentTextChanged.connect(self.invalidate)
        self.target_root.currentTextChanged.connect(self.invalidate)
        self.preview()

    def invalidate(self,*args):
        self.apply_button.setEnabled(False)

    def preview(self):
        try:
            self.plan,skipped=plan_import(self.db,self.source,self.source_root.currentText().strip(),self.target_root.currentText().strip())
        except Exception as exc:
            QMessageBox.warning(self,tr('取り込み失敗'),translate_message(str(exc))); return
        self.table.setRowCount(0)
        self.current.clear(); self.incoming.clear()
        self.table.setRowCount(len(self.plan))
        for index,row in enumerate(self.plan):
            self.table.setItem(index,0,QTableWidgetItem(row['path']))
            tags=QTableWidgetItem(row['merged_tags']); tags.setToolTip(row['tags']+' → '+row['merged_tags'])
            self.table.setItem(index,1,tags)
            if row['conflict']:
                choice=QComboBox()
                for label,value in [('現在を保持','keep'),('取り込み元を採用','replace'),('両方を残す','both')]: choice.addItem(tr(label),value)
                choice.currentIndexChanged.connect(lambda _,r=row,c=choice:r.update(choice=c.currentData()))
                self.table.setCellWidget(index,2,choice)
            else:
                self.table.setItem(index,2,QTableWidgetItem(tr('空欄へ取り込み') if not row['memo'].strip() and row['incoming_memo'].strip() else tr('変更なし')))
        self.summary.setText(tr('照合: {v0}件 / メモ競合: {v1}件 / 対応なし・重複等: {v2}件',v0=len(self.plan),v1=sum(r['conflict'] for r in self.plan),v2=skipped))
        self.apply_button.setEnabled(bool(self.plan))
        if self.plan:self.table.setCurrentCell(0,0)

    def show_notes(self,row,*args):
        if 0<=row<len(self.plan):
            self.current.setPlainText(self.plan[row]['memo'])
            self.incoming.setPlainText(self.plan[row]['incoming_memo'])

    def apply(self):
        self.busy=True
        self.setEnabled(False)
        self.summary.setText(tr('バックアップ・マージ処理中…'))
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.future=self.pool.submit(apply_import,self.db,[dict(row) for row in self.plan])
        self.poll=QTimer(self)
        self.poll.timeout.connect(self.finish_apply)
        self.poll.start(100)

    def finish_apply(self):
        if not self.future.done(): return
        self.poll.stop()
        self.pool.shutdown(wait=False)
        self.busy=False
        self.setEnabled(True)
        try:
            count,backup=self.future.result()
        except Exception as exc:
            self.apply_button.setEnabled(False)
            QMessageBox.warning(self,tr('取り込み失敗'),translate_message(str(exc))); return
        QMessageBox.information(self,tr('取り込み完了'),tr('更新: {v0}件\nバックアップ: {v1}',v0=count,v1=backup or tr('変更なし')))
        self.accept()

    def reject(self):
        if not self.busy: super().reject()

    def closeEvent(self,event):
        if self.busy: event.ignore()
        else: super().closeEvent(event)
