"""Render the original SVG mark into PNG and a Windows multi-size ICO.

Run from any directory with the project's PySide6-Essentials dependency:
    python scripts/make_icon.py

Only the SVG is hand edited. PNG/ICO are deterministic, generated assets.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ASSETS = Path(__file__).resolve().parents[1] / "src" / "harmonica_studio" / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(svg: bytes, size: int) -> bytes:
    """Supersample and strengthen small outlines without changing the mark."""
    if size <= 32:
        # Outlined holes collapse into a dark band at one-pixel resolution.
        # Solid apertures preserve the same six-hole instrument silhouette.
        svg = svg.replace(b'<g fill="none" stroke-width="12">',
                          b'<g fill="#342F29" stroke-width="0">')
    if size <= 16:
        svg = svg.replace(b'stroke-width="12"', b'stroke-width="24"')
        svg = svg.replace(b'stroke-width="9"', b'stroke-width="17"')
    elif size <= 32:
        svg = svg.replace(b'stroke-width="12"', b'stroke-width="18"')
        svg = svg.replace(b'stroke-width="9"', b'stroke-width="13"')
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise ValueError("studio.svg is not valid SVG")
    scale = 4
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Cannot allocate PNG output buffer")
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Cannot encode {size}px icon")
    return bytes(buffer.data())


def main() -> int:
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    svg = (ASSETS / "studio.svg").read_bytes()
    (ASSETS / "studio.png").write_bytes(png_bytes(svg, 512))
    images = [(size, png_bytes(svg, size)) for size in ICO_SIZES]
    # ICONDIR + ICONDIRENTRY headers followed by lossless PNG resources.
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, payload in images:
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                     1, 32, len(payload), offset))
        offset += len(payload)
    (ASSETS / "studio.ico").write_bytes(bytes(directory) + b"".join(p for _, p in images))
    print("Created studio.png (512px) and studio.ico (" + ", ".join(map(str, ICO_SIZES)) + "px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
