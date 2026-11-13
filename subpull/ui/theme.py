"""Visual system: color tokens, frame-type colors, Qt palette and stylesheet.

Sizes in the stylesheet are logical pixels / points. Qt 6 applies the display scale
factor itself, so nothing here is multiplied by a DPI factor.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

T = dict(
    bg="#15171c",          # window background
    surface="#1b1e24",     # panels, tables
    surface2="#22262e",    # inputs, hovered rows, headers
    surface3="#2a2f39",    # pressed / selected chips
    border="#2b3039",
    border_strong="#3a404c",
    text="#e4e7ec",
    text2="#b4bac5",
    muted="#7f8796",
    faint="#596070",
    accent="#5b8def",
    accent_hover="#6f9cf3",
    accent_pressed="#4a7bdc",
    accent_soft="#22324f",
    selection="#253651",
    success="#4fb57e",
    warning="#d9a440",
    danger="#e5554e",
    info="#5b8def",
)

# Restrained, distinguishable hues for frame types (used as small badges, never as large fills).
TYPE_COLORS = {
    "LIGHT": "#e0bf5a",
    "FLAT": "#58b7c9",
    "DARK": "#8b95ab",
    "DARKFLAT": "#7f95d6",
    "BIAS": "#ad86dc",
    "?": "#6c7383",
}

STATUS_COLORS = {
    "new": T["accent"],
    "already local": T["success"],
    "size conflict": T["warning"],
    "partially downloaded": T["accent"],
    "downloaded before (missing)": T["muted"],
    "name clash": T["danger"],
}

QUEUE_STATE_COLORS = {
    "queued": T["muted"],
    "downloading": T["accent"],
    "verifying": T["accent"],
    "done": T["success"],
    "skipped": T["muted"],
    "error": T["danger"],
    "cancelled": T["warning"],
}


def color(name: str) -> QColor:
    return QColor(T.get(name, name))


def tinted(hex_color: str, alpha: float) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(max(8.5, QApplication.font().pointSizeF() - 0.5))
    return f


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    f = app.font()
    if f.pointSizeF() < 9.5:
        f.setPointSizeF(9.5)
    app.setFont(f)

    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: T["bg"], QPalette.ColorRole.WindowText: T["text"],
        QPalette.ColorRole.Base: T["surface"], QPalette.ColorRole.AlternateBase: "#1e2128",
        QPalette.ColorRole.Text: T["text"], QPalette.ColorRole.Button: T["surface2"],
        QPalette.ColorRole.ButtonText: T["text"], QPalette.ColorRole.Highlight: T["selection"],
        QPalette.ColorRole.HighlightedText: T["text"], QPalette.ColorRole.ToolTipBase: T["surface3"],
        QPalette.ColorRole.ToolTipText: T["text"], QPalette.ColorRole.PlaceholderText: T["faint"],
        QPalette.ColorRole.Link: T["accent"], QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Mid: T["border"], QPalette.ColorRole.Dark: T["bg"], QPalette.ColorRole.Light: T["surface3"],
    }
    for role, c in roles.items():
        p.setColor(role, QColor(c))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(T["faint"]))
    app.setPalette(p)
    from .icons import ASSETS
    app.setStyleSheet(stylesheet(ASSETS.as_posix()))


def stylesheet(assets: str) -> str:
    t = T
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t['text']}; }}
QMainWindow, #Page {{ background: {t['bg']}; }}
QToolTip {{ background: {t['surface3']}; color: {t['text']}; border: 1px solid {t['border_strong']};
            padding: 4px 8px; border-radius: 6px; }}

/* ---- sidebar ---------------------------------------------------------- */
#Sidebar {{ background: {t['surface']}; border-right: 1px solid {t['border']}; }}
#Sidebar QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 7px 9px;
                        color: {t['text2']}; text-align: left; }}
#Sidebar QToolButton:hover {{ background: {t['surface2']}; color: {t['text']}; }}
#Sidebar QToolButton:checked {{ background: {t['accent_soft']}; color: {t['text']}; }}
#BrandName {{ font-weight: 600; font-size: 11.5pt; }}
#SidebarFooter {{ color: {t['muted']}; }}

/* ---- top bar ---------------------------------------------------------- */
#TopBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
#Pill {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; }}
#Pill QLabel {{ background: transparent; }}
QToolButton#PillButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
                          padding: 5px 10px; color: {t['text']}; }}
QToolButton#PillButton:hover {{ border-color: {t['border_strong']}; background: {t['surface2']}; }}
QToolButton#PillButton::menu-indicator {{ image: none; width: 0; }}

/* ---- typography ------------------------------------------------------- */
#PageTitle {{ font-size: 15pt; font-weight: 600; }}
#SectionTitle {{ font-size: 11pt; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {t['muted']}; }}
#Faint {{ color: {t['faint']}; }}
#StatValue {{ font-size: 14pt; font-weight: 600; }}
#StatLabel {{ color: {t['muted']}; font-size: 8.5pt; }}
#EmptyTitle {{ font-size: 13pt; font-weight: 600; color: {t['text2']}; }}

/* ---- cards / panels --------------------------------------------------- */
#Card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
#Panel {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
#Banner {{ background: #2b2516; border: 1px solid #4a3d1d; border-radius: 8px; }}
#Banner QLabel {{ color: #e8c77a; background: transparent; }}
#DetailKey {{ color: {t['muted']}; }}

/* ---- buttons ---------------------------------------------------------- */
QPushButton, QToolButton {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 7px;
                            padding: 5px 12px; color: {t['text']}; }}
QPushButton:hover, QToolButton:hover {{ background: {t['surface3']}; border-color: {t['border_strong']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['border']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['faint']}; background: {t['surface']}; border-color: {t['border']}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {t['accent']}; }}
QPushButton#Primary {{ background: {t['accent']}; border: 1px solid {t['accent']}; color: white; font-weight: 600; padding: 6px 16px; }}
QPushButton#Primary:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {t['accent_pressed']}; }}
QPushButton#Primary:disabled {{ background: {t['surface2']}; border-color: {t['border']}; color: {t['faint']}; }}
QPushButton#Danger {{ color: {t['danger']}; }}
QPushButton#Danger:disabled {{ color: {t['faint']}; }}
QPushButton#Ghost, QToolButton#Ghost {{ background: transparent; border-color: transparent; }}
QPushButton#Ghost:hover, QToolButton#Ghost:hover {{ background: {t['surface2']}; }}
QPushButton#Warn {{ background: #2b2516; border-color: #4a3d1d; color: #e8c77a; }}
QPushButton#Warn:hover {{ background: #352d1a; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 6px; }}

/* segmented control + chips */
QPushButton#Segment {{ border-radius: 0; padding: 4px 0px; background: {t['surface']}; color: {t['text2']}; }}
QPushButton#Segment:checked {{ background: {t['surface3']}; color: {t['text']}; }}
QPushButton#SegmentFirst {{ border-top-right-radius: 0; border-bottom-right-radius: 0; padding: 4px 0px;
                            background: {t['surface']}; color: {t['text2']}; }}
QPushButton#SegmentLast {{ border-top-left-radius: 0; border-bottom-left-radius: 0; padding: 4px 0px;
                           background: {t['surface']}; color: {t['text2']}; }}
QPushButton#SegmentFirst:checked, QPushButton#SegmentLast:checked {{ background: {t['surface3']}; color: {t['text']}; }}
QToolButton#Chip {{ border-radius: 12px; padding: 3px 10px; background: transparent; color: {t['muted']}; }}
QToolButton#Chip:checked {{ background: {t['surface2']}; color: {t['text']}; border-color: {t['border_strong']}; }}

/* ---- inputs ----------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background: {t['surface2']}; border: 1px solid {t['border']};
    border-radius: 7px; padding: 4px 8px; selection-background-color: {t['accent']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit:read-only {{ background: {t['surface']}; color: {t['text2']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['surface2']}; border: 1px solid {t['border_strong']};
    selection-background-color: {t['selection']}; padding: 4px; outline: none; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTreeView::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {t['border_strong']}; background: {t['surface2']}; }}
QCheckBox::indicator:hover, QTreeView::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']};
    image: url("{assets}/check-white.svg"); }}
QTreeView::indicator:indeterminate {{ background: {t['accent_soft']}; border-color: {t['accent']};
    image: url("{assets}/dash-accent.svg"); }}

/* ---- item views ------------------------------------------------------- */
QTreeView, QTableView, QListView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px;
    alternate-background-color: #1e2128; gridline-color: transparent; selection-background-color: {t['selection']}; }}
QTreeView::item, QTableView::item {{ padding: 3px 4px; border: none; }}
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {{ background: {t['surface2']}; }}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{ background: {t['selection']}; color: {t['text']}; }}
QTreeView::branch {{ background: transparent; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {t['surface']}; color: {t['muted']}; border: none;
    border-bottom: 1px solid {t['border']}; padding: 6px 8px; font-size: 8.5pt; }}
QHeaderView::section:hover {{ color: {t['text']}; }}
QTableCornerButton::section {{ background: {t['surface']}; border: none; }}

/* ---- scrollbars ------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {t['border_strong']}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {t['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; height: 0; width: 0; }}

/* ---- misc ------------------------------------------------------------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QProgressBar {{ background: {t['surface2']}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QMenu {{ background: {t['surface2']}; border: 1px solid {t['border_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t['selection']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QDialog {{ background: {t['bg']}; }}
QMessageBox {{ background: {t['bg']}; }}
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
"""
