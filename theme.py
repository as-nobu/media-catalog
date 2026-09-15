STYLE = """
QMainWindow { background: #f3f5f9; }
QWidget { color: #24324a; font-family: 'Segoe UI', 'Yu Gothic UI', sans-serif; font-size: 13px; }
QPushButton, QComboBox, QSpinBox { background: white; border: 1px solid #d5deea; border-radius: 6px; padding: 6px 10px; min-height: 20px; }
QPushButton:hover { background: #eaf1ff; border-color: #90afe5; }
QPushButton:pressed, QPushButton:checked { background: #dce9ff; }
QPushButton#primary { background: #2962d9; color: white; border: 1px solid #2962d9; }
QPushButton#primary:hover { background: #214fb0; }
QLineEdit, QPlainTextEdit, QTreeWidget, QListView { background: white; border: 1px solid #dce3ed; border-radius: 8px; selection-background-color: #dce9ff; selection-color: #183e83; }
QLineEdit { padding: 9px 12px; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #6493e9; }
QPlainTextEdit { padding: 8px; }
QTreeWidget::item { padding: 6px 2px; }
QListView::item { border: 1px solid transparent; border-radius: 8px; padding: 5px; }
QListView::item:hover { background: #f0f5ff; }
QListView::item:selected { background: #e4edff; border: 1px solid #8eaee7; }
QHeaderView::section { background: #f6f8fc; color: #65738a; border: none; padding: 9px; }
QCheckBox { spacing: 6px; padding: 3px; }
QSplitter::handle { background: #f3f5f9; }
QSplitter::handle:hover { background: #cedcf2; }
QMenu { background: white; border: 1px solid #d5deea; padding: 5px; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #e4edff; }
QLabel#progress { color: #64748b; padding: 5px; }
QStatusBar { background: #edf1f7; }
"""
