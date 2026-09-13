"""Small appearance/library dialog with reversible live theme preview."""
from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QLineEdit, QPushButton, QFileDialog, QDialogButtonBox)
from .preferences import Preferences
from .theme import THEMES


class SettingsDialog(QDialog):
    themePreview = Signal(str)

    def __init__(self, preferences, library_root, *, busy=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('设置 · 口琴工坊')
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        title = QLabel('设置');title.setObjectName('hero');layout.addWidget(title)
        layout.addWidget(QLabel('主题色'))
        self.theme = QComboBox()
        for key, palette in THEMES.items():
            self.theme.addItem(palette['name'], key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(preferences.theme)))
        self.theme.currentIndexChanged.connect(lambda _: self.themePreview.emit(self.theme.currentData()))
        layout.addWidget(self.theme)
        self.compact = QCheckBox('精简模式 · 选歌后自动生成，隐藏调音与编辑')
        self.compact.setChecked(preferences.compact)
        self.compact.setEnabled(not busy)
        layout.addWidget(self.compact)
        self.skip_long_rests = QCheckBox('跳过长空白')
        self.skip_long_rests.setChecked(preferences.skip_long_rests)
        self.skip_long_rests.setEnabled(not busy)
        layout.addWidget(self.skip_long_rests)
        rest_hint = QLabel('音符之间超过 3 秒的空白缩短为 0.6 秒。保留正常停顿和长音，试听与游戏演奏同步生效。')
        rest_hint.setObjectName('muted');rest_hint.setWordWrap(True);layout.addWidget(rest_hint)
        layout.addWidget(QLabel('曲库文件夹'))
        row = QHBoxLayout()
        self.folder = QLineEdit(str(library_root));self.folder.setReadOnly(True)
        self.folder.setToolTip(str(library_root))
        browse = QPushButton('选择…');browse.clicked.connect(self.choose_folder)
        row.addWidget(self.folder, 1);row.addWidget(browse);layout.addLayout(row)
        hint = QLabel('将 MIDI 放入这个文件夹，曲库会自动更新。支持 .mid / .midi / .kar / .rmi，不扫描子文件夹。')
        hint.setObjectName('muted');hint.setWordWrap(True);layout.addWidget(hint)
        self.use_default = QPushButton('恢复默认曲库文件夹')
        self.use_default.clicked.connect(self.reset_folder);layout.addWidget(self.use_default)
        self._default_folder = not preferences.library_folder
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('保存')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self):
        selected = QFileDialog.getExistingDirectory(self, '选择曲库文件夹', self.folder.text())
        if selected:
            self.folder.setText(selected);self.folder.setToolTip(selected);self._default_folder = False

    def reset_folder(self):
        from .paths import default_library_root
        self.folder.setText(str(default_library_root()));self._default_folder = True

    def preferences(self):
        return Preferences(theme=self.theme.currentData(), compact=self.compact.isChecked(),
                           library_folder='' if self._default_folder else str(Path(self.folder.text())),
                           skip_long_rests=self.skip_long_rests.isChecked())
