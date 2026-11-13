"""Reusable widgets and item delegates. All dimensions derive from font metrics so the
layout follows the platform's font size and display scaling."""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget)

from ..workflow import fmt_size
from . import icons
from .models import GroupRole, LevelRole, ProgressRole, StateRole, StatusRole, SummaryRole, TypeRole
from .theme import QUEUE_STATE_COLORS, STATUS_COLORS, T, TYPE_COLORS, tinted


# =========================================================================== layout helpers
def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it):
    if it is None:
        lay.addStretch(1)
    elif isinstance(it, int):
        lay.addSpacing(it)
    elif isinstance(it, QWidget):
        lay.addWidget(it)
    else:
        lay.addLayout(it)


def label(text: str = "", name: Optional[str] = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(text: str, icon_name: Optional[str] = None, kind: Optional[str] = None, tooltip: str = "",
           slot=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        color = "#ffffff" if kind == "Primary" else (T["danger"] if kind == "Danger" else T["text2"])
        b.setIcon(icons.icon(icon_name, color, "#ffffff" if kind == "Primary" else None))
    if tooltip:
        b.setToolTip(tooltip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def tool_button(icon_name: str, tooltip: str, slot=None, name: str = "Ghost") -> QToolButton:
    b = QToolButton()
    b.setObjectName(name)
    b.setIcon(icons.icon(icon_name))
    b.setToolTip(tooltip)
    b.setAutoRaise(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


class Card(QFrame):
    def __init__(self, name: str = "Card", parent=None):
        super().__init__(parent)
        self.setObjectName(name)


class StatusDot(QWidget):
    """Small colored dot; size follows the font height."""

    def __init__(self, color: str = T["muted"], parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        d = max(8, int(self.fontMetrics().height() * 0.5))
        self.setFixedSize(d, d)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        glow = QColor(self._color)
        glow.setAlphaF(0.25)
        p.setBrush(glow)
        p.drawEllipse(self.rect())
        p.setBrush(self._color)
        m = self.width() * 0.22
        p.drawEllipse(QRectF(self.rect()).adjusted(m, m, -m, -m))


class SearchField(QLineEdit):
    def __init__(self, placeholder: str = "Search", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", T["muted"]), QLineEdit.ActionPosition.LeadingPosition)


class Segmented(QWidget):
    """Exclusive segmented control. Emits ``changed(key)``."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        lay = hbox(spacing=0)
        self.setLayout(lay)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setObjectName("SegmentFirst" if i == 0 else ("SegmentLast" if i == len(options) - 1 else "Segment"))
            b.setChecked(key == current)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            fm = b.fontMetrics()  # extra slack: fractional scaling (125 %, 175 %) rounds glyph advances up
            b.setFixedWidth(int(fm.horizontalAdvance(text) * 1.15) + 2 * fm.averageCharWidth() + 16)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)

    def set_current(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


class TypeChip(QToolButton):
    """Checkable frame-type filter chip with a colored marker."""

    def __init__(self, frame_type: str, text: str, parent=None):
        super().__init__(parent)
        self.frame_type = frame_type
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(True)
        self.setObjectName("Chip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        fm = self.fontMetrics()
        self._dot = max(6, int(fm.height() * 0.38))
        self.setStyleSheet(f"QToolButton#Chip {{ padding-left: {self._dot + 14}px; }}")

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(TYPE_COLORS.get(self.frame_type, T["muted"]))
        if not self.isChecked():
            c.setAlphaF(0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        d = self._dot
        p.drawRoundedRect(QRectF(9, (self.height() - d) / 2, d, d), 2, 2)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, action: Optional[QPushButton] = None, parent=None):
        super().__init__(parent)
        ic = QLabel()
        size = int(self.fontMetrics().height() * 2.6)
        ic.setPixmap(icons.icon(icon_name, T["faint"]).pixmap(QSize(size, size)))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(title, "EmptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = label(text, "Muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setFixedWidth(self.fontMetrics().averageCharWidth() * 64)
        col = vbox(None, ic, 6, self.title, hbox(None, self.text, None), spacing=6)
        if action:
            col.addSpacing(10)
            col.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)
        self.setLayout(col)


class StatTile(QWidget):
    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.value = label("—", "StatValue")
        self.caption = label(caption.upper(), "StatLabel")
        self.setLayout(vbox(self.value, self.caption, spacing=2))


# =========================================================================== delegates
def _badge(painter: QPainter, rect: QRect, text: str, color: str, font: QFont) -> None:
    f = QFont(font)
    f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
    f.setWeight(QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
    fm = QFontMetrics(f)
    h = fm.height() + 2
    w = fm.horizontalAdvance(text) + 12
    r = QRectF(rect.left() + 6, rect.center().y() - h / 2 + 0.5, w, h)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tinted(color, 0.16))
    painter.drawRoundedRect(r, 4, 4)
    painter.setPen(QColor(color))
    painter.setFont(f)
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


def _dot_text(painter: QPainter, rect: QRect, color: str, text: str, font: QFont, text_color: Optional[str] = None) -> None:
    fm = QFontMetrics(font)
    d = max(6.0, fm.height() * 0.4)
    x = rect.left() + 8
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(x, rect.center().y() - d / 2 + 0.5, d, d))
    painter.setPen(QColor(text_color or T["text2"]))
    painter.setFont(font)
    tr = QRect(int(x + d + 7), rect.top(), rect.width() - int(d + 15), rect.height())
    painter.drawText(tr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                     fm.elidedText(text, Qt.TextElideMode.ElideRight, tr.width()))


class _BaseDelegate(QStyledItemDelegate):
    def _background(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        w = option.widget
        style = w.style() if w is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, w)


class FrameDelegate(_BaseDelegate):
    """Frame tree: group header rows, type badges and local-status dots."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        h = int(option.fontMetrics.height() * (2.15 if index.data(GroupRole) else 1.75))
        return QSize(s.width(), max(s.height(), h))

    def paint(self, painter, option, index):
        col = self._model.COLUMNS[index.column()][0]
        if index.data(GroupRole):
            if index.column() == 0:
                self._paint_group(painter, option, index)
            return
        if col == "type":
            self._background(painter, option, index)
            ft = index.data(TypeRole)
            _badge(painter, option.rect, ft, TYPE_COLORS.get(ft, T["muted"]), option.font)
            return
        if col == "local":
            self._background(painter, option, index)
            st = index.data(StatusRole)[0]
            _dot_text(painter, option.rect, STATUS_COLORS.get(st.value, T["muted"]), st.value, option.font,
                      T["text"] if st.value == "new" else T["text2"])
            return
        super().paint(painter, option, index)

    def _paint_group(self, painter, option, index):
        painter.save()
        painter.fillRect(option.rect, QColor(T["surface2"]))
        painter.setPen(QPen(QColor(T["border"]), 1))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        opt.backgroundBrush = QColor(0, 0, 0, 0)
        style = option.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)
        tr = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, option.widget)
        bold = QFont(option.font)
        bold.setWeight(QFont.Weight.DemiBold)
        bold.setPointSizeF(option.font.pointSizeF() + 0.5)
        fmb = QFontMetrics(bold)
        painter.setFont(bold)
        painter.setPen(QColor(T["text"]))
        name_w = min(fmb.horizontalAdvance(text), tr.width() // 2)
        painter.drawText(QRect(tr.left() + 2, tr.top(), name_w + 2, tr.height()),
                         Qt.AlignmentFlag.AlignVCenter, fmb.elidedText(text, Qt.TextElideMode.ElideRight, name_w))
        s = index.data(SummaryRole)
        parts = [f"{s['files']} files", fmt_size(s['size'])]
        if s["new"]:
            parts.append(f"{s['new']} new")
        if s["local"]:
            parts.append(f"{s['local']} local")
        if s["selected"]:
            parts.append(f"{s['selected']} selected")
        painter.setFont(option.font)
        painter.setPen(QColor(T["muted"]))
        x = tr.left() + name_w + 16
        painter.drawText(QRect(x, tr.top(), max(0, tr.right() - x), tr.height()), Qt.AlignmentFlag.AlignVCenter,
                         "  ·  ".join(parts))
        painter.restore()


class ObjectDelegate(QStyledItemDelegate):
    """Two-line object row: name + counts, with a 'new' badge."""

    def sizeHint(self, option, index):
        fm = option.fontMetrics
        return QSize(fm.averageCharWidth() * 24, int(fm.height() * 2.9))

    def paint(self, painter, option, index):
        s = index.data(SummaryRole)
        if s is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T["selection"] if selected else T["surface2"]))
            painter.drawRoundedRect(r, 7, 7)
        fm = option.fontMetrics
        pad = 10
        bold = QFont(option.font)
        bold.setWeight(QFont.Weight.DemiBold if index.data(GroupRole) or selected else QFont.Weight.Medium)
        badge_w = 0
        if s.new:
            bf = QFont(option.font)
            bf.setPointSizeF(max(7.5, bf.pointSizeF() - 1.5))
            bf.setWeight(QFont.Weight.DemiBold)
            bfm = QFontMetrics(bf)
            t = f"{s.new} new"
            badge_w = bfm.horizontalAdvance(t) + 12
            top0 = r.top() + (r.height() - 2 * fm.height() - 2) / 2
            br = QRectF(r.right() - pad - badge_w, top0 + (fm.height() - bfm.height() - 2) / 2, badge_w, bfm.height() + 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tinted(T["accent"], 0.18))
            painter.drawRoundedRect(br, 4, 4)
            painter.setPen(QColor(T["accent_hover"]))
            painter.setFont(bf)
            painter.drawText(br, Qt.AlignmentFlag.AlignCenter, t)
        name_w = int(r.width() - 2 * pad - (badge_w + 8 if badge_w else 0))
        text_w = int(r.width() - 2 * pad)
        top = r.top() + (r.height() - 2 * fm.height() - 2) / 2
        painter.setFont(bold)
        painter.setPen(QColor(T["text"]))
        painter.drawText(QRectF(r.left() + pad, top, name_w, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(bold).elidedText(s.name, Qt.TextElideMode.ElideRight, name_w))
        painter.setFont(option.font)
        painter.setPen(QColor(T["muted"]))
        unit = "object" if index.data(GroupRole) else "session"
        sub = f"{s.files} frames · {fmt_size(s.size)} · {s.sessions} {unit}{'' if s.sessions == 1 else 's'}"
        painter.drawText(QRectF(r.left() + pad, top + fm.height() + 2, text_w, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(sub, Qt.TextElideMode.ElideRight, text_w))
        painter.restore()


class QueueDelegate(_BaseDelegate):
    """Transfer queue: state dots and progress bars."""

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), int(option.fontMetrics.height() * 1.85)))

    def paint(self, painter, option, index):
        c = index.column()
        state = index.data(StateRole)
        color = QUEUE_STATE_COLORS.get(state, T["muted"])
        if c == 2:
            self._background(painter, option, index)
            _dot_text(painter, option.rect, color, state, option.font, T["text"])
            return
        if c == 3:
            self._background(painter, option, index)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            frac = max(0.0, min(1.0, index.data(ProgressRole) or 0.0))
            fm = option.fontMetrics
            pct_w = fm.horizontalAdvance("100%") + 10
            h = max(4.0, fm.height() * 0.32)
            r = QRectF(option.rect.left() + 8, option.rect.center().y() - h / 2 + 0.5,
                       max(10, option.rect.width() - 16 - pct_w), h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(T["surface3"]))
            painter.drawRoundedRect(r, h / 2, h / 2)
            if frac > 0:
                fill = QRectF(r)
                fill.setWidth(max(h, r.width() * frac))
                painter.setBrush(QColor(color if state not in ("queued",) else T["faint"]))
                painter.drawRoundedRect(fill, h / 2, h / 2)
            painter.setPen(QColor(T["text2"]))
            painter.setFont(option.font)
            painter.drawText(QRectF(r.right() + 6, option.rect.top(), pct_w, option.rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{frac * 100:.0f}%")
            painter.restore()
            return
        super().paint(painter, option, index)


class LevelDelegate(_BaseDelegate):
    COLORS = {logging.ERROR: T["danger"], logging.WARNING: T["warning"], logging.INFO: T["accent"], logging.DEBUG: T["faint"]}

    def __init__(self, column: int, parent=None):
        super().__init__(parent)
        self.column = column

    def paint(self, painter, option, index):
        if index.column() != self.column:
            return super().paint(painter, option, index)
        self._background(painter, option, index)
        level = index.data(LevelRole) or logging.INFO
        key = max(k for k in self.COLORS if k <= max(level, logging.DEBUG))
        _dot_text(painter, option.rect, self.COLORS[key], index.data() or "", option.font, T["text2"])


class TypeBadgeDelegate(_BaseDelegate):
    def paint(self, painter, option, index):
        self._background(painter, option, index)
        ft = index.data(TypeRole) or index.data() or "?"
        _badge(painter, option.rect, ft, TYPE_COLORS.get(ft, T["muted"]), option.font)
