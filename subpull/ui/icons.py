"""Vector icons drawn from inline SVG (original, minimal stroke icons).

Icons are rendered at several pixel sizes so Qt picks a crisp pixmap for any
device pixel ratio (100–300 %); no icon font or extra dependency is needed.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import T

ASSETS = Path(__file__).resolve().parent / "assets"

# 24×24 stroke paths; "C" is replaced by the requested color.
_PATHS = {
    "library":  '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9.5h17M9.5 9.5v10"/>',
    "queue":    '<path d="M12 3.5v11m0 0-4-4m4 4 4-4"/><path d="M4.5 15.5v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5"/>',
    "history":  '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "logs":     '<path d="M5 6.5h14M5 12h14M5 17.5h9"/>',
    "settings": '<path d="M4 7.5h9M17 7.5h3M4 16.5h3M11 16.5h9"/><circle cx="15" cy="7.5" r="2"/><circle cx="9" cy="16.5" r="2"/>',
    "scan":     '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4.5H15"/>',
    "stop":     '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    "folder":   '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "open":     '<path d="M14 4.5h5.5V10M19.5 4.5 11 13"/><path d="M18 14v4a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 18V8a1.5 1.5 0 0 1 1.5-1.5H10"/>',
    "server":   '<rect x="3.5" y="4.5" width="17" height="6.5" rx="1.5"/><rect x="3.5" y="13" width="17" height="6.5" rx="1.5"/><path d="M7 7.75h.01M7 16.25h.01"/>',
    "search":   '<circle cx="11" cy="11" r="6.5"/><path d="m19.5 19.5-3.8-3.8"/>',
    "download": '<path d="M12 4v10m0 0-4-4m4 4 4-4"/><path d="M5 19.5h14"/>',
    "x":        '<path d="M7 7l10 10M17 7 7 17"/>',
    "check":    '<path d="m5.5 12.5 4 4 9-9"/>',
    "alert":    '<path d="M12 4.5 20.5 19h-17z"/><path d="M12 10v4M12 16.8v.01"/>',
    "info":     '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.01"/>',
    "error":    '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6m0-6-6 6"/>',
    "retry":    '<path d="M4.5 12a7.5 7.5 0 0 1 12.8-5.3L19.5 9"/><path d="M19.5 4.5V9H15"/><path d="M19.5 12a7.5 7.5 0 0 1-12.8 5.3L4.5 15"/><path d="M4.5 19.5V15H9"/>',
    "clear":    '<path d="M5 7h14M9.5 7V5.5h5V7M7 7l.8 11.5a1.5 1.5 0 0 0 1.5 1.5h5.4a1.5 1.5 0 0 0 1.5-1.5L17 7"/>',
    "key":      '<circle cx="8.5" cy="14.5" r="4"/><path d="m11.5 11.5 8-8M17 6l2.5 2.5M14.5 8.5 16.5 10.5"/>',
    "lock":     '<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    "sidebar":  '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    "columns":  '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15M14.5 4.5v15"/>',
    "chevron":  '<path d="m9 6 6 6-6 6"/>',
    "select":   '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 5-6"/>',
    "fits":     '<rect x="4.5" y="3.5" width="15" height="17" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "target":   '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/>',
}

_SIZES = (16, 18, 20, 24, 28, 32, 40, 48, 64, 72, 96)


def _svg(name: str, color: str, stroke: float = 1.7) -> bytes:
    body = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _render(data: bytes, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(data))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


@lru_cache(maxsize=None)
def icon(name: str, color: str = T["text2"], active: str | None = None) -> QIcon:
    """Icon in ``color``; ``active`` (default: text color) is used for checked/active states."""
    active = active or T["text"]
    ic = QIcon()
    for size in _SIZES:
        ic.addPixmap(_render(_svg(name, color), size), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(_render(_svg(name, active), size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_render(_svg(name, active), size), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(_render(_svg(name, T["faint"]), size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def _logo_svg() -> bytes:
    a = T["accent"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<rect x="2" y="2" width="60" height="60" rx="15" fill="#1f2a3f"/>'
            f'<circle cx="32" cy="27" r="15" fill="none" stroke="{a}" stroke-width="3.2" stroke-dasharray="70 25" '
            f'stroke-linecap="round" transform="rotate(-50 32 27)"/>'
            f'<circle cx="32" cy="27" r="4.2" fill="#e4e7ec"/>'
            f'<path d="M32 36v14m0 0-6-6m6 6 6-6" fill="none" stroke="#e4e7ec" stroke-width="3.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/></svg>').encode()


@lru_cache(maxsize=None)
def logo_icon() -> QIcon:
    ic = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        ic.addPixmap(_render(_logo_svg(), size))
    return ic
