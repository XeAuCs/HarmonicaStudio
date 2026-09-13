"""Local pairing dialog; QR generation and adapter discovery stay offline."""
import ipaddress
import math
import time

import segno
from segno import consts
from PySide6.QtCore import QEvent, QLineF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog,
                              QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QToolButton, QVBoxLayout, QWidget)
from .paths import icon_path
from .theme import theme_palette


def lan_addresses():
    found = []
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if not flags & QNetworkInterface.IsUp or flags & QNetworkInterface.IsLoopBack:
            continue
        name = interface.humanReadableName()
        for entry in interface.addressEntries():
            address = entry.ip()
            if address.protocol() != QAbstractSocket.IPv4Protocol:
                continue
            value = address.toString()
            ip = ipaddress.ip_address(value)
            if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                virtual = any(word in name.lower() for word in ('virtual', 'vmware', 'vethernet', 'vpn', 'tun', 'tailscale'))
                found.append((virtual, name, value))
    return [(name, value) for _, name, value in sorted(set(found))]


QR_TONES = {
    '#9F4937': ('#9F4937', '#794963', '#526741', '#826025'),
    '#386C5F': ('#326855', '#276D79', '#465F91', '#71577C'),
    '#4A6084': ('#385989', '#5E5193', '#2F7273', '#845066'),
    '#805369': ('#805369', '#565A93', '#326D72', '#9B4F64'),
}


def qr_pixmap(url, palette=None, *, artistic=True, device_pixel_ratio=1.0):
    """Round every artistic element; preserve module centers and clear quiet space."""
    code = segno.make_qr(url, error='m')
    matrix = tuple(code.matrix)
    types = tuple(code.matrix_iter(border=0, verbose=True)) if artistic else ()
    modules = len(matrix) + 8
    scale = max(4, 300 // modules)
    ratio = max(1.0, float(device_pixel_ratio))
    size = math.ceil(modules * scale * ratio)
    pixmap = QPixmap(size, size);pixmap.setDevicePixelRatio(ratio)
    colors = palette or dict(theme_palette(), surface='#FFFFFF')
    paper = QColor(colors['surface']) if artistic else QColor('#FFFFFF')
    ink = QColor(colors['ink']) if artistic else QColor('#202020')
    gradient = QLinearGradient(4*scale, 4*scale, (modules-4)*scale, (modules-4)*scale)
    tones = QR_TONES.get(colors['accent'].upper(), (colors['accent'], colors['ink']))
    for index, color in enumerate(tones):gradient.setColorAt(index/(len(tones)-1), QColor(color))
    brush = QBrush(gradient)
    pixmap.fill(paper)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, artistic)
    painter.setPen(Qt.NoPen);painter.setBrush(brush if artistic else QBrush(ink))
    reserved = (consts.TYPE_FINDER_PATTERN_DARK, consts.TYPE_ALIGNMENT_PATTERN_DARK)
    alignment_types = (consts.TYPE_ALIGNMENT_PATTERN_LIGHT, consts.TYPE_ALIGNMENT_PATTERN_DARK)
    alignment = []
    if artistic:
        for y, row in enumerate(types):
            for x, kind in enumerate(row):
                if (kind == consts.TYPE_ALIGNMENT_PATTERN_DARK
                        and (not x or row[x-1] not in alignment_types)
                        and (not y or types[y-1][x] not in alignment_types)):
                    alignment.append((x,y))
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x] or artistic and types[y][x] in reserved:
                x += 1;continue
            if artistic:
                end = x + 1
                while end < len(row) and row[end] and types[y][end] not in reserved:end += 1
                # Separate neighboring rows slightly so their junctions cannot
                # create square inner corners. All runs have semicircular ends,
                # including timing, format, version and fixed dark modules.
                inset = scale*.06
                height = scale-2*inset
                painter.drawRoundedRect(QRectF((x+4)*scale+inset, (y+4)*scale+inset,
                                               (end-x)*scale-2*inset, height), height/2, height/2)
                x = end
            else:
                painter.fillRect((x+4)*scale, (y+4)*scale, scale, scale, ink);x += 1
    if artistic:
        # Preserve the finder 7:5:3 and alignment 5:3:1 geometry, with rounded
        # outer AND inner corners. Smaller alignment centers are full circles.
        for x, y in ((0, 0), (len(matrix)-7, 0), (0, len(matrix)-7)):
            for inset, width, color, radius in ((0,7,brush,1.15),(1,5,QBrush(paper),.85),(2,3,brush,1.0)):
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF((x+4+inset)*scale,(y+4+inset)*scale,width*scale,width*scale),radius*scale,radius*scale)
        for x, y in alignment:
            for inset, width, color, radius in ((0,5,brush,.85),(1,3,QBrush(paper),.65),(2,1,brush,.5)):
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF((x+4+inset)*scale,(y+4+inset)*scale,width*scale,width*scale),radius*scale,radius*scale)
    painter.end()
    return pixmap


class ScoreOrnament(QWidget):
    """Small staff engraving outside the QR's quiet zone."""
    def __init__(self, parent=None):
        super().__init__(parent);self.setFixedHeight(24);self.colors=theme_palette()

    def paintEvent(self, event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        middle=self.width()/2
        painter.setPen(QPen(QColor(self.colors['line']),.8))
        for y in (6,12,18):
            painter.drawLine(QLineF(16,y,middle-46,y))
            painter.drawLine(QLineF(middle+46,y,self.width()-16,y))
        painter.setPen(QPen(QColor(self.colors['accent']),1.2))
        painter.setBrush(QColor(self.colors['accent']))
        for offset, y in ((-22,16),(0,10),(22,13)):
            painter.drawEllipse(QRectF(middle+offset-3,y-2,6,4))
            painter.drawLine(QLineF(middle+offset+3,y,middle+offset+3,y-9))


class RemoteDialog(QDialog):
    def __init__(self, controller, addresses, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('手机遥控 · 口琴工坊')
        self.setFixedWidth(468)
        self.colors=theme_palette(getattr(getattr(parent,'preferences',None),'theme','paper'))
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 20, 24, 22);box.setSpacing(12)
        heading=QHBoxLayout()
        title = QLabel('手机遥控');title.setObjectName('hero');heading.addWidget(title);heading.addStretch()
        self.connection=QLabel('等待手机连接');self.connection.setObjectName('muted');heading.addWidget(self.connection)
        box.addLayout(heading)
        hint = QLabel('扫码，在手机浏览器中选曲与控制播放。')
        hint.setWordWrap(True);hint.setObjectName('muted');box.addWidget(hint)
        self.card=QFrame();self.card.setObjectName('pairingCard')
        cardbox=QVBoxLayout(self.card);cardbox.setContentsMargins(18,12,18,12);cardbox.setSpacing(0)
        brand=QHBoxLayout();brand.setSpacing(8)
        mark=QLabel();mark.setPixmap(QIcon(str(icon_path())).pixmap(30,30));brand.addWidget(mark)
        name=QLabel('口琴工坊');name.setObjectName('section');brand.addWidget(name);brand.addStretch()
        caption=QLabel('随手选曲 · 随时演奏');caption.setObjectName('eyebrow');brand.addWidget(caption)
        cardbox.addLayout(brand)
        self.qr = QLabel();self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setAccessibleName('手机遥控连接二维码');cardbox.addWidget(self.qr)
        self.ornament=ScoreOrnament();cardbox.addWidget(self.ornament)
        box.addWidget(self.card)
        styles=QHBoxLayout();styles.setSpacing(6)
        style_label=QLabel('二维码样式');style_label.setObjectName('muted');styles.addWidget(style_label);styles.addStretch()
        self.style_group=QButtonGroup(self);self.style_group.setExclusive(True)
        self.artistic=QToolButton();self.artistic.setText('线条码')
        self.standard=QToolButton();self.standard.setText('标准码')
        for button in (self.artistic,self.standard):
            button.setCheckable(True);self.style_group.addButton(button);styles.addWidget(button)
        self.artistic.setChecked(True);box.addLayout(styles)
        network=QHBoxLayout();network.setSpacing(12)
        network_label=QLabel('连接网络');network_label.setObjectName('muted');network.addWidget(network_label)
        self.address = QComboBox()
        self.address.setAccessibleName('手机连接网络')
        for name, value in addresses:
            self.address.addItem(f'{name}  /  {value}', value)
        if not addresses:
            self.address.addItem('未找到局域网地址 · 仅本机可访问', '127.0.0.1')
        network.addWidget(self.address,1);box.addLayout(network)
        # Keep the complete URL for copying, without displaying the long pairing key.
        self.url = QLineEdit(self);self.url.setReadOnly(True);self.url.hide()
        help_text = QLabel('手机与电脑需在同一局域网，声音由电脑播放。\n识别不顺时可切换标准码；重新开启遥控后请重新扫码。')
        help_text.setToolTip('连接不上时，检查是否为访客 Wi-Fi，并在 Windows 防火墙提示中允许专用网络。二维码相当于遥控钥匙，请只分享给可信的人。')
        help_text.setWordWrap(True);help_text.setObjectName('muted');box.addWidget(help_text)
        row = QHBoxLayout()
        self.copy_button = QPushButton('复制连接');self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.url.text()))
        hide = QPushButton('收起');hide.clicked.connect(self.hide)
        hide.setObjectName('primary')
        disable = QPushButton('关闭遥控');disable.clicked.connect(self.disable)
        for button in (self.copy_button, hide, disable):row.addWidget(button)
        box.addLayout(row)
        self.address.currentIndexChanged.connect(self.update_qr)
        self.artistic.toggled.connect(self.update_qr)
        self.timer = QTimer(self);self.timer.timeout.connect(self.update_connection);self.timer.start(1000)
        self.set_theme(self.colors)

    def update_qr(self):
        url = self.controller.url(self.address.currentData())
        self.url.setText(url)
        self.qr.setPixmap(qr_pixmap(url,self.colors,artistic=self.artistic.isChecked(),device_pixel_ratio=self.devicePixelRatioF()))

    def set_theme(self, palette):
        self.colors=dict(palette);self.ornament.colors=self.colors;self.ornament.update()
        self.card.setStyleSheet('QFrame#pairingCard { background: '+palette['surface']+'; border: 1px solid '+palette['line']+'; border-radius: 2px; }')
        self.update_qr()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type()==QEvent.DevicePixelRatioChange and hasattr(self,'qr') and hasattr(self,'artistic'):
            self.update_qr()

    def update_connection(self):
        if not self.controller.active:
            self.connection.setText('遥控已关闭');return
        recent = self.controller.last_client_at
        self.connection.setText('手机已连接' if recent and time.time() - recent < 5 else '等待手机连接')

    def disable(self):
        self.parent().stop_remote();self.close()
