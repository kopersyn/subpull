"""Qt item models. They only adapt core data structures for views – no business logic here."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import (QAbstractItemModel, QAbstractListModel, QAbstractTableModel, QModelIndex,
                            QSortFilterProxyModel, Qt, Signal)
from PySide6.QtGui import QColor, QFont

from ..downloader import TransferJob, verified_label
from ..i18n import t
from ..local_state import LocalStatus, status_label
from ..scanner import FrameFile
from ..workflow import (TYPE_ORDER, UNKNOWN_TYPE, ObjectSummary, Status, exposure_seconds, fmt_seconds, fmt_size,
                        fmt_speed, group_files, is_new)
from .theme import T

USER = Qt.ItemDataRole.UserRole
FileRole = USER + 1
GroupRole = USER + 2
StatusRole = USER + 3
TypeRole = USER + 4
SummaryRole = USER + 5
ProgressRole = USER + 6
StateRole = USER + 7
LevelRole = USER + 8
SortRole = USER + 9

DISPLAY = Qt.ItemDataRole.DisplayRole
CHECK = Qt.ItemDataRole.CheckStateRole


def _check_value(value) -> Qt.CheckState:
    if isinstance(value, Qt.CheckState):
        return value
    return Qt.CheckState(int(value))


# =========================================================================== frames
class FrameTreeModel(QAbstractItemModel):
    """Two-level tree: groups (object / session / type) → frame files. Column 0 is checkable;
    group rows are tri-state. Selection lives in a shared ``set`` of remote paths."""

    # (column id, translation key of the header)
    COLUMNS = [("name", "column.file"), ("type", "column.type"), ("filter", "column.filter"),
               ("exposure", "column.exposure"), ("size", "column.size"), ("session", "column.session"),
               ("object", "column.object"), ("local", "column.local"), ("notes", "column.notes")]
    COL = {key: i for i, (key, _) in enumerate(COLUMNS)}

    selectionChanged = Signal()

    def __init__(self, selected: set, parent=None):
        super().__init__(parent)
        self._selected = selected
        self._groups: list[tuple[str, list[FrameFile]]] = []
        self._statuses: dict[str, Status] = {}
        self._group_by = "object"
        self._sort: tuple[int, Qt.SortOrder] = (-1, Qt.SortOrder.AscendingOrder)

    # ------------------------------------------------------------------ population
    def set_files(self, files: list[FrameFile], statuses: dict[str, Status], group_by: str) -> None:
        self.beginResetModel()
        self._statuses = statuses
        self._group_by = group_by
        self._groups = group_files(files, group_by)
        self._apply_sort()
        self.endResetModel()

    def set_statuses(self, statuses: dict[str, Status]) -> None:
        self._statuses = statuses
        self._emit_all()

    def refresh_paths(self, paths: set[str]) -> None:
        for g, (_, files) in enumerate(self._groups):
            rows = [r for r, f in enumerate(files) if f.remote_path in paths]
            if rows:
                parent = self.index(g, 0)
                self.dataChanged.emit(self.index(min(rows), 0, parent),
                                      self.index(max(rows), self.columnCount() - 1, parent))
                self.dataChanged.emit(parent, parent)

    def selection_changed_externally(self) -> None:
        self._emit_all()

    def _emit_all(self) -> None:
        last_col = self.columnCount() - 1
        for g, (_, files) in enumerate(self._groups):
            parent = self.index(g, 0)
            if files:
                self.dataChanged.emit(self.index(0, 0, parent), self.index(len(files) - 1, last_col, parent))
        if self._groups:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._groups) - 1, last_col))

    @property
    def group_by(self) -> str:
        return self._group_by

    def groups(self) -> list[tuple[str, list[FrameFile]]]:
        return self._groups

    def visible_files(self) -> list[FrameFile]:
        return [f for _, files in self._groups for f in files]

    def file_at(self, index: QModelIndex) -> Optional[FrameFile]:
        if index.isValid() and index.internalId() != 0:
            return self._groups[index.internalId() - 1][1][index.row()]
        return None

    def group_key_at(self, index: QModelIndex) -> Optional[str]:
        if index.isValid() and index.internalId() == 0:
            return self._groups[index.row()][0]
        return None

    # ------------------------------------------------------------------ tree structure
    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, 0)
        return self.createIndex(row, column, parent.row() + 1)

    def parent(self, index=QModelIndex()):
        if not index.isValid() or index.internalId() == 0:
            return QModelIndex()
        return self.createIndex(index.internalId() - 1, 0, 0)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._groups)
        if parent.internalId() == 0 and parent.column() == 0:
            return len(self._groups[parent.row()][1])
        return 0

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return t(self.COLUMNS[section][1])
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            if self.COLUMNS[section][0] in ("size", "exposure"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        fl = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            fl |= Qt.ItemFlag.ItemIsUserCheckable
            if index.internalId() == 0:
                fl |= Qt.ItemFlag.ItemIsAutoTristate
        return fl

    # ------------------------------------------------------------------ data
    def _group_check(self, files: list[FrameFile]) -> Qt.CheckState:
        n = sum(1 for f in files if f.remote_path in self._selected)
        if n == 0:
            return Qt.CheckState.Unchecked
        return Qt.CheckState.Checked if n == len(files) else Qt.CheckState.PartiallyChecked

    def group_summary(self, row: int) -> dict:
        key, files = self._groups[row]
        statuses = [self._statuses.get(f.remote_path, (LocalStatus.NEW, None)) for f in files]
        return dict(key=key, files=len(files), size=sum(f.size for f in files),
                    new=sum(1 for s in statuses if is_new(s)),
                    local=sum(1 for s in statuses if s[0] == LocalStatus.PRESENT),
                    selected=sum(1 for f in files if f.remote_path in self._selected))

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        col = self.COLUMNS[index.column()][0]
        if index.internalId() == 0:                                   # group row
            key, files = self._groups[index.row()]
            if role == GroupRole:
                return True
            if col == "name":
                if role == DISPLAY:
                    return key
                if role == CHECK:
                    return self._group_check(files)
                if role == SummaryRole:
                    return self.group_summary(index.row())
            return None

        f = self._groups[index.internalId() - 1][1][index.row()]
        status = self._statuses.get(f.remote_path, (LocalStatus.NEW, None))
        if role == FileRole:
            return f
        if role == GroupRole:
            return False
        if role == StatusRole:
            return status
        if role == TypeRole:
            return f.frame_type or UNKNOWN_TYPE
        if role == CHECK and col == "name":
            return Qt.CheckState.Checked if f.remote_path in self._selected else Qt.CheckState.Unchecked
        if role == DISPLAY:
            return self._display(f, col, status)
        if role == SortRole:
            return self._sort_key(f, col, status)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in ("size", "exposure"):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and col in ("notes", "session", "object", "filter", "exposure"):
            return QColor(T["warning"]) if col == "notes" and f.notes else QColor(T["text2"] if col != "notes" else T["muted"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(f, status)
        return None

    @staticmethod
    def _display(f: FrameFile, col: str, status: Status):
        if col == "name":
            return f.name
        if col == "type":
            return f.frame_type or UNKNOWN_TYPE
        if col == "filter":
            return f.filter or ""
        if col == "exposure":
            e = exposure_seconds(f)
            return "" if e is None else fmt_seconds(e, ".0f" if e >= 10 else "g")
        if col == "size":
            return fmt_size(f.size)
        if col == "session":
            return f.session_date
        if col == "object":
            return f.object_name
        if col == "local":
            return status_label(status[0])
        if col == "notes":
            notes = list(f.notes)
            if f.object_source == "FITS":
                notes.insert(0, t("note.object_from_fits"))
            if f.type_source == "FITS":
                notes.insert(0, t("note.type_from_fits"))
            return " · ".join(notes)
        return None

    @staticmethod
    def _sort_key(f: FrameFile, col: str, status: Status):
        return {
            "name": f.name.lower(), "type": TYPE_ORDER.get(f.frame_type or UNKNOWN_TYPE, 9),
            "filter": (f.filter or "~").lower(), "exposure": exposure_seconds(f) or -1.0, "size": f.size,
            "session": f.session_date, "object": f.object_name.lower(), "local": status[0].value,
            "notes": len(f.notes),
        }[col]

    @staticmethod
    def _tooltip(f: FrameFile, status: Status) -> str:
        lines = [f.remote_path, f"{t('column.local')}: {status_label(status[0])}" + (f" – {status[1]}" if status[1] else "")]
        lines += f.notes
        return "\n".join(lines)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != CHECK or not index.isValid() or index.column() != 0:
            return False
        checked = _check_value(value) == Qt.CheckState.Checked
        if index.internalId() == 0:
            # clicking a group selects all of its files unless they are all selected already
            files = self._groups[index.row()][1]
            target = not all(f.remote_path in self._selected for f in files)
            for f in files:
                (self._selected.add if target else self._selected.discard)(f.remote_path)
            if files:
                self.dataChanged.emit(self.index(0, 0, index), self.index(len(files) - 1, 0, index))
            self.dataChanged.emit(index, index)
        else:
            f = self.file_at(index)
            (self._selected.add if checked else self._selected.discard)(f.remote_path)
            self.dataChanged.emit(index, index)
            parent = index.parent()
            self.dataChanged.emit(parent, parent)
        self.selectionChanged.emit()
        return True

    def toggle(self, indexes: list[QModelIndex]) -> None:
        """Toggle several rows at once (keyboard Space). Groups expand to their files."""
        paths: list[str] = []
        for ix in indexes:
            if ix.internalId() == 0:
                paths += [f.remote_path for f in self._groups[ix.row()][1]]
            else:
                paths.append(self.file_at(ix).remote_path)
        if not paths:
            return
        target = not all(p in self._selected for p in paths)
        for p in paths:
            (self._selected.add if target else self._selected.discard)(p)
        self.refresh_paths(set(paths))
        self.selectionChanged.emit()

    # ------------------------------------------------------------------ sorting
    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.beginResetModel()
        self._sort = (column, order)
        self._apply_sort()
        self.endResetModel()

    def _apply_sort(self) -> None:
        column, order = self._sort
        if column < 0:
            return
        col = self.COLUMNS[column][0]
        rev = order == Qt.SortOrder.DescendingOrder
        for _, files in self._groups:
            files.sort(key=lambda f: self._sort_key(f, col, self._statuses.get(f.remote_path, (LocalStatus.NEW, None))),
                       reverse=rev)


# =========================================================================== objects
class ObjectListModel(QAbstractListModel):
    """'All objects' followed by one row per object (with counts)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Optional[ObjectSummary]] = []
        self._total: Optional[ObjectSummary] = None

    def set_summaries(self, summaries: list[ObjectSummary]) -> None:
        self.beginResetModel()
        self._total = ObjectSummary(t("library.objects.all"), sum(s.files for s in summaries), sum(s.new for s in summaries),
                                    sum(s.size for s in summaries), len(summaries), {}) if summaries else None
        self._rows = ([None] + list(summaries)) if summaries else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def object_at(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._rows) and self._rows[row] is not None:
            return self._rows[row].name
        return None

    def row_of(self, name: Optional[str]) -> int:
        for i, s in enumerate(self._rows):
            if (s is None and name is None) or (s is not None and s.name == name):
                return i
        return 0 if self._rows else -1

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        summary = s if s is not None else self._total
        if role == DISPLAY:
            return summary.name
        if role == SummaryRole:
            return summary
        if role == GroupRole:
            return s is None
        if role == Qt.ItemDataRole.ToolTipRole and s is not None:
            types = ", ".join(f"{k} {v}" for k, v in sorted(s.types.items(), key=lambda kv: TYPE_ORDER.get(kv[0], 9)))
            counts = " · ".join((t("count.frames", count=s.files), t("count.sessions", count=s.sessions), fmt_size(s.size)))
            return f"{s.name}\n{counts}\n{types}"
        return None


# =========================================================================== transfer queue
class QueueModel(QAbstractTableModel):
    COLUMNS = ["column.file", "column.object", "column.status", "column.progress", "column.speed", "column.size",
               "column.info"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ids: list[int] = []
        self._jobs: dict[int, TransferJob] = {}

    def jobs(self) -> list[TransferJob]:
        return [self._jobs[i] for i in self._ids]

    def job_at(self, row: int) -> Optional[TransferJob]:
        return self._jobs.get(self._ids[row]) if 0 <= row < len(self._ids) else None

    def update_job(self, job: TransferJob) -> None:
        if job.id in self._jobs:
            self._jobs[job.id] = job
            row = self._ids.index(job.id)
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.COLUMNS) - 1))
        else:
            self.beginInsertRows(QModelIndex(), len(self._ids), len(self._ids))
            self._ids.append(job.id)
            self._jobs[job.id] = job
            self.endInsertRows()

    def remove_ids(self, ids: set[int]) -> None:
        if not ids:
            return
        self.beginResetModel()
        self._ids = [i for i in self._ids if i not in ids]
        for i in ids:
            self._jobs.pop(i, None)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._ids)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return t(self.COLUMNS[section])
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        j = self._jobs[self._ids[index.row()]]
        c = index.column()
        if role == ProgressRole:
            return (j.done / j.total) if j.total else (1.0 if j.state == "done" else 0.0)
        if role == StateRole:
            return j.state
        if role == DISPLAY:
            return [j.file.name, j.file.object_name, t(f"transfer.state.{j.state}"), "",
                    fmt_speed(j.speed) if j.state == "downloading" and j.speed else "",
                    fmt_size(j.total), j.message][c]
        if role == Qt.ItemDataRole.ForegroundRole and c in (1, 4, 5, 6):
            return QColor(T["text2"] if c != 6 else T["muted"])
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (4, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{j.file.remote_path}\n→ {j.dest}\n{j.message}".strip()
        return None


# =========================================================================== history
class HistoryModel(QAbstractTableModel):
    COLUMNS = ["column.downloaded", "column.object", "column.file", "column.type", "column.size", "column.session",
               "column.verified", "column.local_path"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return t(self.COLUMNS[section])
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()
        if role == DISPLAY:
            return [datetime.fromtimestamp(r["downloaded_at"]).strftime("%Y-%m-%d %H:%M"), r["object"], r["filename"],
                    r["frame_type"] or UNKNOWN_TYPE, fmt_size(r["size"] or 0), r["session_date"] or "",
                    verified_label(r["verified"]), r["local_path"]][c]
        if role == SortRole:
            return [r["downloaded_at"], r["object"].lower(), r["filename"].lower(), r["frame_type"] or "",
                    r["size"] or 0, r["session_date"] or "", r["verified"] or "", r["local_path"]][c]
        if role == TypeRole:
            return r["frame_type"] or UNKNOWN_TYPE
        if role == Qt.ItemDataRole.ForegroundRole and c in (0, 5, 6, 7):
            return QColor(T["text2"] if c != 7 else T["muted"])
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 4:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{r['remote_path']}\n→ {r['local_path']}\nSHA-256: {r['sha256'] or '—'}"
        return None


class OperationsModel(QAbstractTableModel):
    """Scan / download batch summaries and transfer errors from the history database."""
    COLUMNS = ["column.time", "column.event", "column.summary"]
    KINDS = ("scan", "download", "error")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return t(self.COLUMNS[section])
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        if role == DISPLAY:
            kind = t(f"history.event.{r['kind']}") if r["kind"] in self.KINDS else r["kind"]
            return [datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M"), kind, r["summary"]][index.column()]
        if role == LevelRole:
            return logging.ERROR if r["kind"] == "error" else logging.INFO
        if role == Qt.ItemDataRole.ForegroundRole:
            if r["kind"] == "error" and index.column() > 0:
                return QColor(T["danger"])
            return QColor(T["text2"] if index.column() < 2 else T["text"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return r["summary"]
        return None


# =========================================================================== logs
class LogModel(QAbstractTableModel):
    COLUMNS = ["column.time", "column.level", "column.source", "column.message"]
    LEVELS = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warning", logging.ERROR: "error",
              logging.CRITICAL: "critical"}
    MAX = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[float, int, str, str]] = []

    def append(self, ts: float, level: int, source: str, message: str) -> None:
        if len(self._rows) >= self.MAX:
            drop = len(self._rows) - self.MAX + 500
            self.beginRemoveRows(QModelIndex(), 0, drop - 1)
            del self._rows[:drop]
            self.endRemoveRows()
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append((ts, level, source, message))
        self.endInsertRows()

    def counts(self) -> tuple[int, int]:
        return (sum(1 for r in self._rows if r[1] >= logging.ERROR),
                sum(1 for r in self._rows if logging.WARNING <= r[1] < logging.ERROR))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return t(self.COLUMNS[section])
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        ts, level, source, msg = self._rows[index.row()]
        if role == DISPLAY:
            name = t(f"logs.level.{self.LEVELS[level]}") if level in self.LEVELS else logging.getLevelName(level).title()
            return [datetime.fromtimestamp(ts).strftime("%H:%M:%S"), name, source, msg][index.column()]
        if role == LevelRole:
            return level
        if role == Qt.ItemDataRole.ForegroundRole:
            if index.column() in (0, 2):
                return QColor(T["muted"])
            if level >= logging.ERROR:
                return QColor(T["danger"])
            if level >= logging.WARNING:
                return QColor(T["warning"])
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 3:
            return msg
        return None


class LogFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_level = logging.INFO
        self.text = ""

    def set_filter(self, min_level: int, text: str) -> None:
        if hasattr(self, "beginFilterChange"):          # Qt >= 6.9
            self.beginFilterChange()
            self.min_level, self.text = min_level, text.lower()
            self.endFilterChange()
        else:
            self.min_level, self.text = min_level, text.lower()
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        m = self.sourceModel()
        if m.index(row, 0, parent).data(LevelRole) < self.min_level:
            return False
        if self.text:
            return any(self.text in (m.index(row, c, parent).data() or "").lower() for c in (2, 3))
        return True


class TextFilterProxy(QSortFilterProxyModel):
    """Case-insensitive search across all columns; sorts by SortRole when the model provides it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.setSortRole(SortRole)

    def lessThan(self, left, right):
        a, b = left.data(SortRole), right.data(SortRole)
        if a is None or b is None:
            return (left.data() or "") < (right.data() or "")
        return a < b
