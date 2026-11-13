"""UI-independent workflow logic: filtering, grouping, download planning and queue statistics.

Everything here is plain Python so it can be unit-tested without a GUI toolkit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import local_state
from .downloader import ACTIVE, CANCELLED, DONE, ERROR, QUEUED, SKIPPED, VERIFYING, TransferJob
from .local_state import InvalidName, LocalStatus
from .scanner import FrameFile, ScanResult

UNKNOWN_TYPE = "?"
NEW_STATES = (LocalStatus.NEW, LocalStatus.PARTIAL)
# Statuses the user must opt in to before they are downloaded (default: skipped).
OPT_IN_STATES = (LocalStatus.PRESENT, LocalStatus.CONFLICT, LocalStatus.HISTORY_ONLY)
OVERWRITE_STATES = (LocalStatus.PRESENT, LocalStatus.CONFLICT)

Status = tuple[LocalStatus, Optional[str]]


# --------------------------------------------------------------------------- local status
def compute_statuses(files: Iterable[FrameFile], history_rows: dict, dest_root: Optional[Path]) -> dict[str, Status]:
    files = list(files)
    if dest_root is None:
        return {f.remote_path: (LocalStatus.NEW, "destination folder not set") for f in files}
    clashes = local_state.name_clashes(files, dest_root)
    return {f.remote_path: local_state.local_status(f, history_rows, clashes, dest_root) for f in files}


def is_new(status: Status) -> bool:
    return status[0] in NEW_STATES


# --------------------------------------------------------------------------- filtering / grouping
@dataclass
class FileFilter:
    object_name: Optional[str] = None          # exact object, None = all
    object_query: str = ""                     # substring on object names (used when object_name is None)
    types: Optional[set[str]] = None           # frame types incl. "?" for unknown; None = all
    session: Optional[str] = None              # exact session date, None = all
    new_only: bool = False
    text: str = ""                             # substring on file names

    def matches(self, f: FrameFile, status: Status) -> bool:
        if self.object_name is not None and f.object_name != self.object_name:
            return False
        if self.object_name is None and self.object_query and self.object_query.lower() not in f.object_name.lower():
            return False
        if self.types is not None and (f.frame_type or UNKNOWN_TYPE) not in self.types:
            return False
        if self.session is not None and f.session_date != self.session:
            return False
        if self.new_only and not is_new(status):
            return False
        if self.text and self.text.lower() not in f.name.lower():
            return False
        return True


def filter_files(files: Iterable[FrameFile], statuses: dict[str, Status], flt: FileFilter) -> list[FrameFile]:
    default = (LocalStatus.NEW, None)
    return [f for f in files if flt.matches(f, statuses.get(f.remote_path, default))]


TYPE_ORDER = {"LIGHT": 0, "FLAT": 1, "DARK": 2, "DARKFLAT": 3, "BIAS": 4, UNKNOWN_TYPE: 5}


def group_files(files: Iterable[FrameFile], by: str) -> list[tuple[str, list[FrameFile]]]:
    """Group by 'object' (A→Z), 'session' (newest first) or 'type' (LIGHT, FLAT, DARK…)."""
    groups: dict[str, list[FrameFile]] = {}
    for f in files:
        key = {"object": f.object_name, "session": f.session_date}.get(by, f.frame_type or UNKNOWN_TYPE)
        groups.setdefault(key, []).append(f)
    if by == "object":
        keys = sorted(groups, key=str.lower)
    elif by == "session":
        keys = sorted(groups, reverse=True)
    else:
        keys = sorted(groups, key=lambda k: TYPE_ORDER.get(k, 9))
    for k in keys:
        groups[k].sort(key=lambda f: (f.session_date, TYPE_ORDER.get(f.frame_type or UNKNOWN_TYPE, 9), f.name))
    return [(k, groups[k]) for k in keys]


@dataclass
class ObjectSummary:
    name: str
    files: int
    new: int
    size: int
    sessions: int
    types: dict[str, int]


def object_summaries(result: ScanResult, statuses: dict[str, Status]) -> list[ObjectSummary]:
    out = []
    for name, fl in sorted(result.objects.items(), key=lambda kv: kv[0].lower()):
        types: dict[str, int] = {}
        for f in fl:
            types[f.frame_type or UNKNOWN_TYPE] = types.get(f.frame_type or UNKNOWN_TYPE, 0) + 1
        out.append(ObjectSummary(
            name=name, files=len(fl),
            new=sum(1 for f in fl if is_new(statuses.get(f.remote_path, (LocalStatus.NEW, None)))),
            size=sum(f.size for f in fl), sessions=len({f.session_date for f in fl}), types=types))
    return out


def exposure_seconds(f: FrameFile) -> Optional[float]:
    v = (f.fits or {}).get("EXPTIME")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- download planning
@dataclass
class PlanRow:
    object_name: str
    count: int = 0
    size: int = 0
    skipped: int = 0
    dest: Optional[Path] = None
    dest_error: Optional[str] = None
    new_folder: bool = False


@dataclass
class DownloadPlan:
    todo: list[FrameFile] = field(default_factory=list)
    skipped: list[tuple[FrameFile, str]] = field(default_factory=list)
    rows: list[PlanRow] = field(default_factory=list)
    counts: dict[LocalStatus, int] = field(default_factory=dict)   # selected files per status
    overwrite_count: int = 0

    @property
    def size(self) -> int:
        return sum(f.size for f in self.todo)


def build_plan(selected: Iterable[FrameFile], statuses: dict[str, Status], dest_root: Path,
               include: Iterable[LocalStatus] = (), active_paths: Iterable[str] = ()) -> DownloadPlan:
    """Decide which selected files will be downloaded. Opt-in statuses (already local, size
    conflict, downloaded before) are skipped unless listed in ``include``; name clashes and
    files already queued are always skipped."""
    include, active = set(include), set(active_paths)
    plan = DownloadPlan()
    rows: dict[str, PlanRow] = {}
    for f in selected:
        st = statuses.get(f.remote_path, (LocalStatus.NEW, None))[0]
        plan.counts[st] = plan.counts.get(st, 0) + 1
        row = rows.setdefault(f.object_name, PlanRow(f.object_name))
        if f.remote_path in active:
            reason = "already in the transfer queue"
        elif st == LocalStatus.NAME_CLASH:
            reason = "name clash or invalid name"
        elif st in OPT_IN_STATES and st not in include:
            reason = st.value
        else:
            reason = None
        if reason:
            plan.skipped.append((f, reason))
            row.skipped += 1
            continue
        plan.todo.append(f)
        row.count += 1
        row.size += f.size
        if st in OVERWRITE_STATES:
            plan.overwrite_count += 1
    for name in sorted(rows, key=str.lower):
        row = rows[name]
        try:
            row.dest = local_state.dest_dir(name, dest_root)
            row.new_folder = not row.dest.parent.exists()
        except InvalidName as e:
            row.dest_error = str(e)
        plan.rows.append(row)
    return plan


def make_jobs(files: Iterable[FrameFile], statuses: dict[str, Status], dest_root: Path,
              overwrite_states: Iterable[LocalStatus] = ()) -> tuple[list[TransferJob], list[str]]:
    """Transfer jobs for the planned files. ``overwrite`` is set only for files whose status
    the user explicitly allowed to be overwritten AND whose destination exists."""
    overwrite_states = set(overwrite_states)
    jobs, errors = [], []
    for f in sorted(files, key=lambda f: (f.object_name.lower(), f.session_date, f.name)):
        try:
            dest = local_state.dest_path(f.object_name, f.name, dest_root)
        except InvalidName as e:
            errors.append(str(e))
            continue
        st = statuses.get(f.remote_path, (LocalStatus.NEW, None))[0]
        jobs.append(TransferJob(f, dest, overwrite=st in overwrite_states and dest.exists()))
    return jobs, errors


# --------------------------------------------------------------------------- queue statistics
@dataclass
class QueueStats:
    total_files: int = 0
    done: int = 0
    skipped: int = 0
    errors: int = 0
    cancelled: int = 0
    active: int = 0
    queued: int = 0
    total_bytes: int = 0
    done_bytes: int = 0
    remaining_bytes: int = 0
    speed: float = 0.0

    @property
    def fraction(self) -> float:
        return self.done_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.speed > 0 and self.remaining_bytes:
            return self.remaining_bytes / self.speed
        return None

    @property
    def running(self) -> bool:
        return bool(self.active or self.queued)

    @classmethod
    def from_jobs(cls, jobs: Iterable[TransferJob]) -> "QueueStats":
        s = cls()
        for j in jobs:
            s.total_files += 1
            if j.state == SKIPPED:
                s.skipped += 1
                continue
            s.total_bytes += j.total
            if j.state == DONE:
                s.done += 1
                s.done_bytes += j.total
                continue
            s.done_bytes += j.done
            if j.state == ERROR:
                s.errors += 1
            elif j.state == CANCELLED:
                s.cancelled += 1
            elif j.state in (ACTIVE, VERIFYING):
                s.active += 1
                s.speed += j.speed if j.state == ACTIVE else 0.0
                s.remaining_bytes += max(0, j.total - j.done)
            elif j.state == QUEUED:
                s.queued += 1
                s.remaining_bytes += max(0, j.total - j.done)
        return s


# --------------------------------------------------------------------------- formatting helpers
def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def scan_summary(result: ScanResult, elapsed: float) -> str:
    total = sum(f.size for f in result.files)
    return (f"{len(result.files)} frames · {len(result.objects)} objects · {result.date_dirs} sessions · "
            f"{fmt_size(total)} · {len(result.unrecognized)} unrecognized · {elapsed:.1f} s"
            + (" · CANCELLED" if result.cancelled else ""))
