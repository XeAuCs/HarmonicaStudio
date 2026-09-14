"""Small header actions with scalable, theme-aware line icons."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton
from .theme import theme_palette


class IconButton(QPushButton):
    def __init__(self, name, icon, callback):
        super().__init__('')
        self._name, self._icon = name, icon
        self._active = False
        self._colors = theme_palette()
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.clicked.connect(callback)

    def set_theme(self, colors):
        self._colors = dict(colors)
        self.update()

    def set_active(self, active):
        self._active = bool(active)
        description = self._name + (' · 已开启' if active else '')
        self.setToolTip(description)
        self.setAccessibleName(description)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate((self.width() - 24) / 2, (self.height() - 24) / 2)
        color = QColor(self._colors['accent' if self._active else 'ink'] if self.isEnabled()
                       else self._colors['muted'])
        painter.setPen(QPen(color, 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if self._icon == 'phone':
            painter.drawRoundedRect(QRectF(6, 2, 12, 20), 2.2, 2.2)
            painter.drawLine(QPointF(10, 5), QPointF(14, 5))
            painter.drawLine(QPointF(11, 19), QPointF(13, 19))
        else:
            gear = QPainterPath()
            for index in range(32):
                angle = math.tau * index / 32 - math.pi / 16
                radius = 9.5 if index % 4 in (0, 1) else 7.3
                point = QPointF(12 + radius * math.cos(angle), 12 + radius * math.sin(angle))
                gear.moveTo(point) if index == 0 else gear.lineTo(point)
            gear.closeSubpath()
            painter.drawPath(gear)
            painter.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        if self._active:
            painter.setPen(QPen(QColor(self._colors['surface']), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(21, 3), 3, 3)
