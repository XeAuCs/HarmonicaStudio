"""Quiet, paper-like palettes shared by the window and the score editor."""
from __future__ import annotations

THEMES = {
    "paper": {
        "name": "暖纸 · 朱砂", "bg": "#F4F1EA", "surface": "#FCFAF5",
        "ink": "#292720", "muted": "#6E695F", "line": "#CFC8BB", "grid": "#E5DED2",
        "accent": "#9F4937", "accent_soft": "#EFE0D8", "accent_ink": "#FFFAF4",
        "selection": "#E6D8CC", "playhead": "#B15339",
    },
    "forest": {
        "name": "雾绿 · 松石", "bg": "#EDF0EB", "surface": "#F8FAF5",
        "ink": "#25322E", "muted": "#616F66", "line": "#C4CEC4", "grid": "#DFE6DC",
        "accent": "#386C5F", "accent_soft": "#DDE9DE", "accent_ink": "#F8FCF6",
        "selection": "#CDDFD0", "playhead": "#A1683E",
    },
    "blue": {
        "name": "灰蓝 · 靛墨", "bg": "#EEF0F2", "surface": "#FAFAFC",
        "ink": "#29313D", "muted": "#656E7D", "line": "#C8CDD6", "grid": "#E1E5EC",
        "accent": "#4A6084", "accent_soft": "#E0E6EF", "accent_ink": "#FBFCFF",
        "selection": "#D1DCEE", "playhead": "#AC6842",
    },
    "plum": {
        "name": "素绢 · 梅紫", "bg": "#F2EEEF", "surface": "#FCF9FA",
        "ink": "#352C34", "muted": "#756873", "line": "#D0C5CD", "grid": "#E9DFE5",
        "accent": "#805369", "accent_soft": "#ECDDDF", "accent_ink": "#FFFAFC",
        "selection": "#E2CBD5", "playhead": "#AF684A",
    },
}

def theme_palette(name: str = "paper") -> dict[str, str]:
    """Return an independent palette; unknown saved theme names use paper."""
    return dict(THEMES.get(name, THEMES["paper"]))

def make_style(name: str = "paper") -> str:
    p = theme_palette(name)
    return """
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px; color: %(ink)s;
    selection-background-color: %(selection)s; selection-color: %(ink)s;
}
QMainWindow, QDialog, QWidget#root { background: %(bg)s; }
QFrame#card, QFrame[role="card"] {
    background: %(surface)s; border: 1px solid %(line)s; border-radius: 2px;
}
QFrame#divider, QFrame[role="divider"] { background: %(line)s; border: none; }
QLabel { background: transparent; border: none; }
QLabel#brand, QLabel[role="brand"] {
    font-size: 22px; font-weight: 600; color: %(ink)s; letter-spacing: 2px;
}
QLabel#hero, QLabel[role="hero"] { font-size: 20px; font-weight: 500; }
QLabel#section, QLabel[role="section"] { font-size: 14px; font-weight: 600; }
QLabel#muted, QLabel[role="muted"] { font-size: 12px; color: %(muted)s; }
QLabel#result, QLabel[role="result"] { font-size: 13px; font-weight: 500; color: %(ink)s; }
QLabel#eyebrow, QLabel[role="eyebrow"] { font-size: 10px; color: %(muted)s; letter-spacing: 2px; }
QLabel:disabled { color: %(muted)s; }
QPushButton, QToolButton {
    background: %(surface)s; border: 1px solid %(line)s; border-radius: 2px;
    padding: 6px 11px; min-height: 23px; font-weight: 500;
}
QPushButton:hover, QToolButton:hover { background: %(accent_soft)s; border-color: %(accent)s; }
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {
    background: %(selection)s; border-color: %(accent)s;
}
QPushButton:focus, QToolButton:focus { border-color: %(accent)s; }
QPushButton#primary, QPushButton[role="primary"] {
    background: %(accent)s; color: %(accent_ink)s; border-color: %(accent)s; font-weight: 600;
}
QPushButton#primary:hover, QPushButton[role="primary"]:hover { background: %(ink)s; border-color: %(ink)s; }
QPushButton#primary:pressed, QPushButton[role="primary"]:pressed { background: %(muted)s; }
QPushButton:disabled, QToolButton:disabled, QPushButton#primary:disabled {
    background: %(bg)s; color: %(muted)s; border-color: %(grid)s;
}
QToolButton { padding: 4px 8px; }
QToolButton::menu-indicator { subcontrol-position: right center; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {
    background: %(surface)s; border: 1px solid %(line)s; border-radius: 2px;
    padding: 5px 8px; min-height: 24px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: %(bg)s; color: %(muted)s; border-color: %(grid)s;
}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 19px; border: none; border-left: 1px solid %(line)s;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-up-16.png); width: 9px; height: 9px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-16.png); width: 9px; height: 9px;
}
QComboBox { padding-right: 25px; }
QComboBox::drop-down { width: 23px; border: none; border-left: 1px solid %(line)s; }
QComboBox::down-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-16.png); width: 10px; height: 10px;
}
QComboBox QAbstractItemView {
    background: %(surface)s; border: 1px solid %(line)s;
    selection-background-color: %(selection)s; selection-color: %(ink)s; outline: 0;
}
QPlainTextEdit, QTextEdit {
    background: %(surface)s; border: 1px solid %(line)s; border-radius: 2px; padding: 6px;
}
QCheckBox, QRadioButton { spacing: 8px; min-height: 25px; background: transparent; }
QCheckBox:disabled, QRadioButton:disabled { color: %(muted)s; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked { background: %(surface)s; border: 1px solid %(line)s; border-radius: 1px; }
QCheckBox::indicator:checked { background: %(accent)s; border: 1px solid %(accent)s; border-radius: 1px; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:disabled { background: %(grid)s; border-color: %(line)s; }
QRadioButton::indicator:unchecked { background: %(surface)s; border: 1px solid %(line)s; border-radius: 7px; }
QRadioButton::indicator:checked { background: %(accent)s; border: 1px solid %(accent)s; border-radius: 7px; }
QTableView, QTableWidget, QListView, QListWidget, QTreeView, QTreeWidget {
    background: %(surface)s; alternate-background-color: %(bg)s;
    border: 1px solid %(line)s; border-radius: 0; gridline-color: %(grid)s;
    selection-background-color: %(selection)s; selection-color: %(ink)s; outline: 0;
}
QTableView::item, QTableWidget::item { padding: 5px 7px; border-bottom: 1px solid %(grid)s; }
QTableView::item:selected, QTableWidget::item:selected,
QListView::item:selected, QTreeView::item:selected { background: %(selection)s; color: %(ink)s; }
QTableView::item:hover, QTableWidget::item:hover { background: %(accent_soft)s; }
QHeaderView { background: %(bg)s; }
QHeaderView::section {
    background: %(bg)s; color: %(muted)s; font-size: 12px; font-weight: 400;
    border: none; border-bottom: 1px solid %(line)s; padding: 7px 8px;
}
QTableCornerButton::section { background: %(bg)s; border: none; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 8px 14px; color: %(muted)s; margin-right: 3px;
}
QTabBar::tab:selected { color: %(ink)s; border-bottom-color: %(accent)s; }
QTabBar::tab:hover { color: %(accent)s; background: %(accent_soft)s; }
QGroupBox { border: 1px solid %(line)s; border-radius: 2px; margin-top: 16px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 9px; padding: 0 5px; }
QMenuBar { background: %(bg)s; border-bottom: 1px solid %(line)s; }
QMenuBar::item { padding: 6px 10px; background: transparent; }
QMenuBar::item:selected { background: %(accent_soft)s; }
QMenu { background: %(surface)s; border: 1px solid %(line)s; padding: 5px; }
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 1px; }
QMenu::item:selected { background: %(selection)s; color: %(ink)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(line)s; margin: 4px 7px; }
QSlider::groove:horizontal { height: 3px; background: %(line)s; border: none; }
QSlider::sub-page:horizontal { background: %(accent)s; }
QSlider::add-page:horizontal { background: %(line)s; }
QSlider::handle:horizontal {
    background: %(surface)s; border: 2px solid %(accent)s;
    width: 9px; height: 9px; margin: -5px 0; border-radius: 1px;
}
QSlider::handle:horizontal:hover { background: %(accent_soft)s; }
QSlider::handle:horizontal:disabled { border-color: %(line)s; }
QProgressBar { border: none; background: %(grid)s; border-radius: 0; max-height: 4px; color: transparent; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border: none; border-radius: 0; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: %(bg)s; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: %(line)s; min-height: 28px; border-radius: 1px; }
QScrollBar:horizontal { background: %(bg)s; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: %(line)s; min-width: 28px; border-radius: 1px; }
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: %(line)s; width: 1px; height: 1px; }
QStatusBar { background: %(bg)s; border-top: 1px solid %(line)s; color: %(muted)s; }
QStatusBar::item { border: none; }
QToolTip { background: %(surface)s; color: %(ink)s; border: 1px solid %(line)s; padding: 5px 8px; border-radius: 1px; }
""" % p

STYLE = make_style("paper")
